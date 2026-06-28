"""Forms for the member hub."""

from __future__ import annotations

from datetime import time
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

from django import forms
from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify

if TYPE_CHECKING:
    from django.contrib.auth.models import User

from core.html_sanitize import sanitize_rich_html
from core.models import CalendarFeed, SiteConfiguration
from core.widgets import RichTextEditorWidget
from membership.models import (
    CommunityEvent,
    Guild,
    GuildAnnouncement,
    GuildFAQItem,
    GuildLink,
    GuildMeetingNote,
    GuildMeetingNoteAttachment,
    GuildOrientationSettings,
    Member,
    MemberSkill,
    OrientationAvailability,
    OrientationSlot,
    Skill,
    SkillCategory,
    VotingSettings,
)


def _meeting_time_label(hour24: int, minute: int) -> str:
    """Render a 24h time as a friendly 12h label, e.g. (18, 0) -> '6:00 PM'."""
    hour12 = hour24 % 12 or 12
    suffix = "AM" if hour24 < 12 else "PM"
    return f"{hour12}:{minute:02d} {suffix}"


# Preset meeting times on the half hour, 6:00 AM through 9:30 PM — one easy dropdown.
_MEETING_TIME_CHOICES: list[tuple[str, str]] = [("", "—")] + [
    (f"{hour:02d}:{minute:02d}", _meeting_time_label(hour, minute)) for hour in range(6, 22) for minute in (0, 30)
]


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
        choices=_WEEK_CHOICES, coerce=int, required=False, empty_value=None, label="Week of month"
    )
    meeting_time_choice = forms.ChoiceField(choices=_MEETING_TIME_CHOICES, required=False, label="Meeting time")

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
            "meeting_location",
            "meeting_next_override",
            "meeting_is_tba",
            "meeting_schedule",
            "contact_email",
            "discord_url",
            "discord_webhook_url",
            "discord_post_enabled",
            "website_url",
            "show_members",
            "featured_class",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Guild name"}),
            "discord_webhook_url": forms.URLInput(attrs={"placeholder": "https://discord.com/api/webhooks/..."}),
            "about": forms.Textarea(
                attrs={"rows": 5, "placeholder": "Tell members what this guild is about..."},
            ),
            "calendar_url": forms.URLInput(attrs={"placeholder": "https://calendar.google.com/calendar/ical/..."}),
            "calendar_color": forms.TextInput(
                attrs={"type": "color", "class": "pl-color-input"},
            ),
            "youtube_url": forms.URLInput(attrs={"placeholder": "https://youtube.com/watch?v=..."}),
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
            "meeting_location": "Meeting location",
            "meeting_next_override": "Override next date (optional)",
            "meeting_is_tba": "No meeting scheduled yet (show TBA)",
            "meeting_schedule": "Meeting notes",
            "contact_email": "Contact email",
            "discord_url": "Discord channel link (shown to members)",
            "discord_webhook_url": "Announcement webhook (auto-posts here — keep private)",
            "discord_post_enabled": "Also post to our Discord",
            "website_url": "Website URL",
            "show_members": "Show members roster",
            "featured_class": "Featured class",
        }
        help_texts = {
            "banner_image": "Shown at the top of the guild page. Max 5 MB.",
            "calendar_url": (
                "In Google Calendar → Settings → your calendar → 'Secret address in iCal format'. "
                "Leave blank if you don't use Google Calendar."
            ),
            "calendar_color": "Color used for your guild's events on the Community Calendar.",
            "discord_url": "The public invite/link to your channel, shown as a button on your guild page.",
            "discord_webhook_url": (
                "A private Discord webhook for your channel. Don't paste your public invite link here. "
                "Blank = nothing posts to your channel."
            ),
        }

    # Accepted Discord webhook hosts. A mis-pasted public invite link (or any other
    # URL) is rejected here so a bad webhook surfaces at save time instead of failing
    # silently later (the broadcast is best-effort — logged, never shown to the lead).
    _WEBHOOK_PREFIXES = (
        "https://discord.com/api/webhooks/",
        "https://discordapp.com/api/webhooks/",
    )

    def clean_meeting_cadence(self) -> str:
        # An omitted/blank cadence means "no regular meeting", not an empty string.
        return self.cleaned_data.get("meeting_cadence") or Guild.MeetingCadence.NONE

    def clean_discord_webhook_url(self) -> str:
        """Validate the webhook is a Discord webhook URL (or blank).

        Blank is allowed (the guild simply posts nothing to its own channel). A
        non-blank value must be a Discord webhook — not the public invite link that
        belongs in the separate ``discord_url`` field — so a lead never publishes a
        secret webhook on the public guild page or saves a URL that silently fails.
        """
        url = (self.cleaned_data.get("discord_webhook_url") or "").strip()
        if url and not url.startswith(self._WEBHOOK_PREFIXES):
            raise forms.ValidationError(
                "That doesn't look like a Discord webhook. Paste the private webhook URL "
                "(https://discord.com/api/webhooks/...), not your channel's public invite link."
            )
        return url

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        from classes.models import ClassOffering

        featured = cast(forms.ModelChoiceField, self.fields["featured_class"])
        if self.instance and self.instance.pk:
            featured.queryset = ClassOffering.objects.filter(
                category__guild=self.instance, status=ClassOffering.Status.PUBLISHED
            )
        else:
            featured.queryset = ClassOffering.objects.none()

        # Seed the time dropdown from the stored 24h meeting_time, preserving an
        # off-grid value (not on the half hour, or outside 6 AM–9:30 PM) as its own option.
        existing = self.instance.meeting_time if self.instance and self.instance.pk else None
        if existing is not None:
            key = existing.strftime("%H:%M")
            choices = list(_MEETING_TIME_CHOICES)
            if key not in {value for value, _ in choices}:
                choices.append((key, _meeting_time_label(existing.hour, existing.minute)))
            field = cast(forms.ChoiceField, self.fields["meeting_time_choice"])
            field.choices = choices
            field.initial = key

    def save(self, commit: bool = True) -> Guild:
        guild = super().save(commit=False)
        choice = self.cleaned_data.get("meeting_time_choice")
        if choice:
            hour_str, minute_str = choice.split(":")
            guild.meeting_time = time(int(hour_str), int(minute_str))
        else:
            guild.meeting_time = None
        if commit:
            guild.save()
        return guild


class ProfileSettingsForm(forms.ModelForm):
    """Form for editing member profile fields plus per-field directory visibility."""

    VISIBILITY_PREFIX = "show_"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
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
            "open_for_commissions",
            "commission_note",
            "instructor_website",
            "instructor_social_handle",
        ]
        widgets = {
            "preferred_name": forms.TextInput(attrs={"placeholder": "How should we call you?"}),
            "phone": forms.TextInput(attrs={"placeholder": "(optional)"}),
            "discord_handle": forms.TextInput(attrs={"placeholder": "@username"}),
            "other_contact_info": forms.TextInput(attrs={"placeholder": "Instagram, Signal, etc."}),
            "about_me": forms.Textarea(attrs={"rows": 3, "placeholder": "Tell other members a bit about yourself..."}),
            "commission_note": forms.Textarea(
                attrs={
                    "rows": 2,
                    "placeholder": "e.g. Small custom woodworking, websites, AI consulting — happy to chat!",
                }
            ),
            "instructor_social_handle": forms.TextInput(attrs={"placeholder": "@handle"}),
        }
        labels = {
            "show_in_directory": "Show me in the member directory",
            "discord_handle": "Discord",
            "other_contact_info": "Other contact info",
            "about_me": "About me",
            "profile_photo": "Profile photo",
            "open_for_commissions": "Open for commissions",
            "commission_note": "What kind of work do you welcome?",
            "instructor_website": "Website",
            "instructor_social_handle": "Social handle",
        }
        help_texts = {
            "profile_photo": "Optional. Shown next to your name in the member directory. Max 5 MB.",
            "instructor_website": "Shown on your public instructor profile.",
            "instructor_social_handle": "Shown on your public instructor profile.",
        }


class MemberSkillForm(forms.Form):
    """Add a single skill to a member's profile, with optional years of experience."""

    skill = forms.ModelChoiceField(queryset=Skill.objects.none())
    years_experience = forms.IntegerField(required=False, min_value=0, max_value=99)

    def __init__(self, *args: Any, member: Member, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.member = member
        self.fields["skill"].queryset = Skill.objects.filter(status=Skill.Status.APPROVED)  # type: ignore[attr-defined]

    def clean(self) -> dict[str, Any]:
        cleaned: dict[str, Any] = super().clean() or {}
        skill = cleaned.get("skill")
        if skill and self.member.skills.filter(skill=skill).exists():
            raise forms.ValidationError("You've already listed that skill.")
        if self.member.skills.count() >= Member.MAX_SKILLS:
            raise forms.ValidationError(f"You can list up to {Member.MAX_SKILLS} skills.")
        return cleaned

    def save(self) -> MemberSkill:
        return MemberSkill.objects.create(
            member=self.member,
            skill=self.cleaned_data["skill"],
            years_experience=self.cleaned_data.get("years_experience"),
        )


class SkillSuggestionForm(forms.Form):
    """Suggest a new skill not yet in the vocabulary; created pending admin approval."""

    name = forms.CharField(max_length=80)

    def __init__(self, *args: Any, member: Member, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.member = member

    def clean_name(self) -> str:
        name = self.cleaned_data["name"].strip()
        if Skill.objects.filter(name__iexact=name).exists():
            raise forms.ValidationError("That skill already exists — pick it from the list instead.")
        return name

    def save(self) -> MemberSkill:
        name = self.cleaned_data["name"]
        category, _ = SkillCategory.objects.get_or_create(
            slug="suggested", defaults={"name": "Suggested", "sort_order": 999}
        )
        skill = Skill.objects.create(
            name=name,
            slug=slugify(name),
            category=category,
            status=Skill.Status.PENDING,
            suggested_by=self.member,
        )
        return MemberSkill.objects.create(member=self.member, skill=skill)


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
        """Send the feedback email to the configured recipients.

        Routes through the ``core.email.send`` choke-point (Decision 8) so the
        send is audited in ``TransactionalEmailLog`` instead of bypassing it via
        Django's ``send_mail``. Best-effort: a failed feedback email is logged but
        must not 500 the feedback page.
        """
        from core.email import send as send_email

        category_label = dict(self.CATEGORY_CHOICES)[self.cleaned_data["category"]]
        subject = f"[Beta {category_label}] {self.cleaned_data['subject']}"
        body = (
            f"From: {user.get_full_name() or user.email} ({user.email})\n"
            f"Category: {category_label}\n\n"
            f"{self.cleaned_data['message']}"
        )
        send_email(
            to=list(settings.BETA_FEEDBACK_EMAILS),
            subject=subject,
            trigger_kind="hub.beta_feedback",
            text_body=body,
            best_effort=True,
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

    def __init__(self, *args: Any, **kwargs: Any) -> None:
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
            "tab_payments_enabled",
            "class_registration_enabled",
            "class_registration_disabled_note",
        ]
        widgets = {
            "classes_calendar_color": forms.TextInput(attrs={"type": "color"}),
            "class_registration_disabled_note": forms.Textarea(attrs={"rows": 3}),
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
    """A single FAQ question/answer row on the guild edit page.

    Beyond the text answer, a row may add a YouTube embed and at most one document
    (an uploaded file OR a link). The XOR guard mirrors ``GuildMeetingNoteAttachmentForm``.
    """

    class Meta:
        model = GuildFAQItem
        fields = ["question", "answer", "video_url", "document", "document_url", "sort_order"]
        widgets = {
            "answer": forms.Textarea(attrs={"rows": 3}),
            "video_url": forms.URLInput(attrs={"placeholder": "https://youtube.com/watch?v=…"}),
            "document_url": forms.URLInput(attrs={"placeholder": "https://docs.google.com/…"}),
            "sort_order": forms.HiddenInput(),
        }
        labels = {
            "video_url": "Video (YouTube)",
            "document": "Document (upload)",
            "document_url": "…or document link",
        }

    def clean_video_url(self) -> str:
        """Accept only a YouTube URL (or blank) so the answer can embed it."""
        from classes.templatetags.classes_tags import youtube_embed_id

        url = (self.cleaned_data.get("video_url") or "").strip()
        if url and not youtube_embed_id(url):
            raise forms.ValidationError(
                "Enter a YouTube URL — e.g. https://www.youtube.com/watch?v=… or https://youtu.be/…"
            )
        return url

    def clean(self) -> dict[str, Any]:
        cleaned = cast(dict[str, Any], super().clean())
        # Rows flagged for deletion skip the check — mirrors the meeting-note form.
        if cleaned.get("DELETE"):
            return cleaned
        if cleaned.get("document") and cleaned.get("document_url"):
            raise forms.ValidationError("Add a document OR a link for this answer, not both.")
        return cleaned


GuildFAQItemFormSet = forms.inlineformset_factory(Guild, GuildFAQItem, form=GuildFAQItemForm, extra=0, can_delete=True)


class GuildLinkForm(forms.ModelForm):
    """A single external-link row on the guild edit page."""

    class Meta:
        model = GuildLink
        fields = ["label", "url", "sort_order"]
        widgets = {"sort_order": forms.HiddenInput()}


GuildLinkFormSet = forms.inlineformset_factory(Guild, GuildLink, form=GuildLinkForm, extra=0, can_delete=True)


class GuildMeetingNoteForm(forms.ModelForm):
    """The note's own fields (date, title, Markdown body). ``guild``/``created_by`` set in the view."""

    class Meta:
        model = GuildMeetingNote
        fields = ["meeting_date", "title", "body"]
        widgets = {
            "meeting_date": forms.DateInput(attrs={"type": "date"}),
            "body": forms.Textarea(attrs={"rows": 6}),
        }


class GuildMeetingNoteAttachmentForm(forms.ModelForm):
    """A single attachment row — exactly one of file / url, enforced here (the user-facing guard)."""

    class Meta:
        model = GuildMeetingNoteAttachment
        fields = ["label", "file", "url", "sort_order"]
        widgets = {"sort_order": forms.HiddenInput()}

    def clean(self) -> dict[str, Any]:
        cleaned = cast(dict[str, Any], super().clean())
        # Rows flagged for deletion skip the check — mirrors OrientationAvailabilityForm.
        if cleaned.get("DELETE"):
            return cleaned
        has_file = bool(cleaned.get("file"))
        has_url = bool(cleaned.get("url"))
        if has_file == has_url:  # both empty or both filled
            raise forms.ValidationError("Each attachment needs exactly one of: an uploaded file OR a link.")
        return cleaned


GuildMeetingNoteAttachmentFormSet = forms.inlineformset_factory(
    GuildMeetingNote,
    GuildMeetingNoteAttachment,
    form=GuildMeetingNoteAttachmentForm,
    extra=0,
    can_delete=True,
)


class GuildOrientationSettingsForm(forms.ModelForm):
    """Edit a guild's orientation config plus its two lead-authored follow-up emails.

    Enabling either follow-up email requires a subject and a body, mirroring the
    instructor welcome-email form. Saving stamps each email's ``*_updated_at``.
    """

    class Meta:
        model = GuildOrientationSettings
        fields = [
            "is_enabled",
            "allow_custom_requests",
            "info",
            "default_seats",
            "default_location",
            "default_duration_minutes",
            "is_closed",
            "closed_message",
            "thankyou_email_enabled",
            "thankyou_email_subject",
            "thankyou_email_body",
            "join_email_enabled",
            "join_email_subject",
            "join_email_body",
        ]
        widgets = {
            "info": forms.Textarea(attrs={"rows": 4}),
            "closed_message": forms.TextInput(attrs={"placeholder": "On vacation till Sept 8"}),
            "thankyou_email_body": RichTextEditorWidget(attrs={"rows": 6}),
            "join_email_body": RichTextEditorWidget(attrs={"rows": 6}),
        }
        labels = {
            "is_enabled": "Offer orientation booking on this guild's page",
            "allow_custom_requests": "Let members propose their own orientation time",
            "info": "Orientation info",
            "default_seats": "Default seats per slot",
            "default_location": "Default location",
            "default_duration_minutes": "Default length (minutes)",
            "is_closed": "Temporarily closed for orientations",
            "closed_message": "Closed message",
            "thankyou_email_enabled": "Send a thank-you / next-steps email after orientation",
            "thankyou_email_subject": "Thank-you subject",
            "thankyou_email_body": "Thank-you message",
            "join_email_enabled": "Send a welcome email when a member joins this guild",
            "join_email_subject": "Welcome subject",
            "join_email_body": "Welcome message",
        }

    def clean_thankyou_email_body(self) -> str:
        return sanitize_rich_html(self.cleaned_data.get("thankyou_email_body") or "")

    def clean_join_email_body(self) -> str:
        return sanitize_rich_html(self.cleaned_data.get("join_email_body") or "")

    def _require_subject_and_body(self, cleaned: dict[str, Any], prefix: str, label: str) -> None:
        if cleaned.get(f"{prefix}_enabled"):
            if not (cleaned.get(f"{prefix}_subject") or "").strip():
                self.add_error(f"{prefix}_subject", f"Add a subject before turning the {label} email on.")
            if not (cleaned.get(f"{prefix}_body") or "").strip():
                self.add_error(f"{prefix}_body", f"Add a message before turning the {label} email on.")

    def clean(self) -> dict[str, Any]:
        cleaned = cast(dict[str, Any], super().clean())
        self._require_subject_and_body(cleaned, "thankyou_email", "thank-you")
        self._require_subject_and_body(cleaned, "join_email", "welcome")
        return cleaned

    _THANKYOU_EMAIL_FIELDS = ("thankyou_email_enabled", "thankyou_email_subject", "thankyou_email_body")
    _JOIN_EMAIL_FIELDS = ("join_email_enabled", "join_email_subject", "join_email_body")

    def save(self, commit: bool = True) -> GuildOrientationSettings:
        now = timezone.now()
        changed = set(self.changed_data)
        if changed.intersection(self._THANKYOU_EMAIL_FIELDS):
            self.instance.thankyou_email_updated_at = now
        if changed.intersection(self._JOIN_EMAIL_FIELDS):
            self.instance.join_email_updated_at = now
        return cast(GuildOrientationSettings, super().save(commit=commit))


class OrientationAvailabilityForm(forms.ModelForm):
    """A single recurring orientation-availability row."""

    class Meta:
        model = OrientationAvailability
        fields = ["weekday", "start_time", "end_time", "seats", "is_active"]
        widgets = {
            "start_time": forms.TimeInput(attrs={"type": "time", "onclick": "this.showPicker?.()"}),
            "end_time": forms.TimeInput(attrs={"type": "time", "onclick": "this.showPicker?.()"}),
        }

    def clean(self) -> dict[str, Any]:
        cleaned = cast(dict[str, Any], super().clean())
        start = cleaned.get("start_time")
        end = cleaned.get("end_time")
        if start and end and end <= start:
            self.add_error("end_time", "End time must be after the start time.")
        return cleaned


OrientationAvailabilityFormSet = forms.inlineformset_factory(
    Guild, OrientationAvailability, form=OrientationAvailabilityForm, extra=0, can_delete=True
)


class OrientationSlotForm(forms.ModelForm):
    """Add a one-off orientation slot from the config editor."""

    class Meta:
        model = OrientationSlot
        fields = ["starts_at", "ends_at", "seats", "location"]
        widgets = {
            "starts_at": forms.DateTimeInput(
                attrs={"type": "datetime-local", "onclick": "this.showPicker?.()"}, format="%Y-%m-%dT%H:%M"
            ),
            "ends_at": forms.DateTimeInput(
                attrs={"type": "datetime-local", "onclick": "this.showPicker?.()"}, format="%Y-%m-%dT%H:%M"
            ),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for name in ("starts_at", "ends_at"):
            cast(forms.DateTimeField, self.fields[name]).input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"]

    def clean(self) -> dict[str, Any]:
        cleaned = cast(dict[str, Any], super().clean())
        starts = cleaned.get("starts_at")
        ends = cleaned.get("ends_at")
        if starts and ends and ends <= starts:
            self.add_error("ends_at", "End must be after the start.")
        if starts and starts <= timezone.now():
            self.add_error("starts_at", "Pick a time in the future.")
        return cleaned


class CommunityEventForm(forms.ModelForm):
    """Add/edit a FOG-native community event.

    One form serves both surfaces: a guild lead (``as_admin=False``) authors their
    guild's events (``event_type``/``guild`` are implied by context and removed from the
    form), while an admin (``as_admin=True``) authors site-wide events and picks the
    type/guild. The datetime widgets are copied from :class:`OrientationSlotForm`.
    """

    class Meta:
        model = CommunityEvent
        fields = ["event_type", "guild", "title", "starts_at", "ends_at", "location", "description", "recurrence"]
        widgets = {
            "starts_at": forms.DateTimeInput(
                attrs={"type": "datetime-local", "onclick": "this.showPicker?.()"}, format="%Y-%m-%dT%H:%M"
            ),
            "ends_at": forms.DateTimeInput(
                attrs={"type": "datetime-local", "onclick": "this.showPicker?.()"}, format="%Y-%m-%dT%H:%M"
            ),
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args: Any, guild: Guild | None = None, as_admin: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for name in ("starts_at", "ends_at"):
            cast(forms.DateTimeField, self.fields[name]).input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"]
        self._as_admin = as_admin
        self._fixed_guild = guild
        if not as_admin:
            del self.fields["event_type"]
            del self.fields["guild"]
        else:
            self.fields["guild"].required = False

    def clean(self) -> dict[str, Any]:
        cleaned = cast(dict[str, Any], super().clean())
        starts = cleaned.get("starts_at")
        ends = cleaned.get("ends_at")
        if starts and ends and ends <= starts:
            self.add_error("ends_at", "End time must be after the start.")
        if self._as_admin:
            etype = cleaned.get("event_type")
            guild = cleaned.get("guild")
            if etype == CommunityEvent.EventType.GUILD_MEETING and guild is None:
                self.add_error("guild", "Pick a guild for a guild event.")
            site_wide = {CommunityEvent.EventType.LEAD_MEETING, CommunityEvent.EventType.COMMUNITY}
            if etype in site_wide and guild is not None:
                self.add_error("guild", "Leave the guild blank for a site-wide event.")
        return cleaned


class OrientationCustomRequestForm(forms.Form):
    """A member proposing their own orientation time when no posted slot works."""

    starts_at = forms.DateTimeField(
        label="Preferred time",
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"],
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local", "onclick": "this.showPicker?.()"}, format="%Y-%m-%dT%H:%M"
        ),
    )
    note = forms.CharField(label="Note (optional)", required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def clean_starts_at(self) -> Any:
        starts = self.cleaned_data["starts_at"]
        if starts <= timezone.now():
            raise forms.ValidationError("Pick a time in the future.")
        return starts


class OrientationAddMemberForm(forms.Form):
    """Admin/lead adds a member to an orientation slot from the dashboard."""

    member = forms.ModelChoiceField(
        queryset=Member.objects.filter(status=Member.Status.ACTIVE).order_by("full_legal_name"),
        label="Member",
    )
    slot = forms.ModelChoiceField(queryset=OrientationSlot.objects.none(), label="Slot")

    def __init__(self, *args: Any, slot_queryset: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if slot_queryset is not None:
            cast(forms.ModelChoiceField, self.fields["slot"]).queryset = slot_queryset


class GuildStaffAddForm(forms.Form):
    """Lead/admin/staff assigns a member a guild staff entry — a preset role or a free-text custom title.

    Each entry is *either* one preset role (co-lead/secretary/treasurer/orienter) *or* one custom title
    (e.g. "Studio Technician"), never both. Titles are cosmetic — every staff entry grants the same authority.
    """

    member = forms.ModelChoiceField(queryset=Member.objects.none(), label="Member")
    role = forms.ChoiceField(choices=[], label="Role", required=False)
    custom_title = forms.CharField(
        max_length=60,
        required=False,
        label="…or type a custom title",
        widget=forms.TextInput(attrs={"maxlength": 60, "placeholder": "e.g. Studio Technician"}),
    )

    def __init__(self, *args: Any, member_queryset: Any = None, guild: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        from membership.models import GuildStaffMembership

        self._guild = guild
        cast(forms.ChoiceField, self.fields["role"]).choices = [
            ("", "Choose a role…"),
            *GuildStaffMembership.Role.choices,
        ]
        if member_queryset is not None:
            cast(forms.ModelChoiceField, self.fields["member"]).queryset = member_queryset

    def clean(self) -> dict[str, Any]:
        from membership.models import GuildStaffMembership

        cleaned: dict[str, Any] = super().clean() or {}
        role = cleaned.get("role") or ""
        custom_title = (cleaned.get("custom_title") or "").strip()
        cleaned["custom_title"] = custom_title

        if role and custom_title:
            raise forms.ValidationError("Pick a preset role or type a custom title — not both.")
        if not role and not custom_title:
            raise forms.ValidationError("Pick a role or type a custom title.")

        member = cleaned.get("member")
        if self._guild is not None and member is not None:
            held = self._guild.staff_memberships.filter(member=member)
            if role and held.filter(role=role).exists():
                label = GuildStaffMembership.Role(role).label
                raise forms.ValidationError(f"{member.display_name} is already {label} of this guild.")
            if custom_title and held.filter(custom_title__iexact=custom_title).exists():
                raise forms.ValidationError(f"{member.display_name} already holds the title “{custom_title}”.")
        return cleaned


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
    """Admin form to broadcast a site-wide announcement to activated members.

    Drives the Site Settings → Announcements composer (preview-then-send). ``body``
    accepts simple HTML (paragraphs / links) — it rides into the branded email shell.
    """

    title = forms.CharField(max_length=300, label="Subject")
    body = forms.CharField(
        widget=RichTextEditorWidget(attrs={"rows": 10}),
        label="Message",
        help_text="Use the toolbar to format — bold, headings, lists, and links. The formatted version "
        "goes out by email; the bell and Discord get a plain-text version.",
    )
    post_to_discord = forms.BooleanField(
        required=False,
        initial=True,
        label="Also post to Discord",
        help_text="Leave on for normal announcements. Turn OFF when sending the release notes — "
        "the release is already posted to Discord automatically when the code goes live.",
    )

    def clean_body(self) -> str:
        body = sanitize_rich_html(self.cleaned_data["body"])
        if not body:
            raise forms.ValidationError("Add a message before sending.")
        return body


class VotingSettingsForm(forms.ModelForm):
    """Admin form for the VotingSettings singleton (the Voting → Settings tab)."""

    class Meta:
        model = VotingSettings
        fields = [
            "reminder_lead_days",
            "minimum_pool_floor",
            "reminders_enabled",
            "send_vote_soon_enabled",
            "auto_snapshot_enabled",
        ]
        labels = {
            "reminder_lead_days": "Reminder lead time (days)",
            "minimum_pool_floor": "Minimum pool floor ($)",
            "reminders_enabled": "Send 'Polls closing soon!' reminders",
            "send_vote_soon_enabled": "Send 'Vote soon!' nudges",
            "auto_snapshot_enabled": "Auto-take the cycle-end snapshot",
        }

    def clean_reminder_lead_days(self) -> int:
        days = self.cleaned_data["reminder_lead_days"]
        if days < 1:
            raise forms.ValidationError("Send the reminder at least 1 day before close.")
        return days

    def clean_minimum_pool_floor(self) -> Decimal:
        floor = self.cleaned_data["minimum_pool_floor"]
        if floor < 0:
            raise forms.ValidationError("The pool floor can't be negative.")
        return floor
