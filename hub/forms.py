"""Forms for the member hub."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

from django import forms
from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from django.http import HttpRequest

    from classes.models import ClassOffering

from core.html_sanitize import sanitize_rich_html
from core.models import CalendarFeed, ScheduledJobState, SiteConfiguration
from core.widgets import PageContentEditorWidget, RichTextEditorWidget
from membership.markdown import sanitize_page_submission
from membership.models import (
    AdminCapability,
    CommunityEvent,
    DiscordGuildEmoji,
    Equipment,
    Floorplan,
    Guild,
    GuildAnnouncement,
    GuildFAQItem,
    GuildLink,
    GuildMailingListEmail,
    GuildMeetingNote,
    GuildMeetingNoteAttachment,
    GuildOrientationSettings,
    HelpCategory,
    MapHotspot,
    MeetingAttachment,
    MeetingItemProposal,
    Member,
    MemberContact,
    MemberSkill,
    OrgFAQItem,
    OrgInfoPage,
    OrgLink,
    OrientationAvailability,
    OrientationAvailabilityBlock,
    OrientationSlot,
    OrientationType,
    Skill,
    SkillCategory,
    Space,
    SpaceRequest,
    SlideshowSlide,
    SlideshowZone,
    VotingSettings,
    WikiArticle,
)


def _meeting_time_label(hour24: int, minute: int) -> str:
    """Render a 24h time as a friendly 12h label, e.g. (18, 0) -> '6:00 PM'."""
    hour12 = hour24 % 12 or 12
    suffix = "AM" if hour24 < 12 else "PM"
    return f"{hour12}:{minute:02d} {suffix}"


def half_hour_time_choices(required: bool) -> list[tuple[str, str]]:
    """Half-hour time-of-day slots, 6:00 AM–9:30 PM, as ("HH:MM", "6:00 AM") pairs.

    The one blessed source for every time-of-day picker (Rule 19: plain half-hour
    ``<select>``, never a per-minute ``type="time"`` input). A leading blank option is
    included only for optional fields.
    """
    slots = [
        (f"{hour:02d}:{minute:02d}", _meeting_time_label(hour, minute)) for hour in range(6, 22) for minute in (0, 30)
    ]
    return slots if required else [("", "—"), *slots]


def _parse_time_choice(value: str) -> time:
    """Parse an "HH:MM" half-hour choice into a ``datetime.time``."""
    hour_str, minute_str = value.split(":")
    return time(int(hour_str), int(minute_str))


def _seed_time_choice(form: forms.BaseForm, name: str, value: time) -> None:
    """Select ``value`` in a half-hour ``<select>``, preserving an off-grid legacy time.

    Real data may hold a :15 time from before the half-hour dropdowns; append it as its
    own option and seed it as the field/form initial so it round-trips untouched until the
    user picks a new time. Writing the form-level initial too makes it win over a
    ModelForm's instance-derived value.
    """
    field = cast(forms.ChoiceField, form.fields[name])
    key = value.strftime("%H:%M")
    choices = cast("list[tuple[str, str]]", field.choices)
    if key not in {choice_value for choice_value, _ in choices}:
        field.choices = [*choices, (key, _meeting_time_label(value.hour, value.minute))]
    field.initial = key
    form.initial[name] = key


# Preset meeting times on the half hour, 6:00 AM through 9:30 PM — one easy dropdown.
_MEETING_TIME_CHOICES: list[tuple[str, str]] = half_hour_time_choices(required=False)


class _FeaturedClassChoiceField(forms.ModelChoiceField):
    """Featured class picker options labeled with the next session date.

    Past runs keep PUBLISHED forever and duplicate_as_new_run reuses the title
    verbatim, so two runs of the same class are indistinguishable without a date.
    """

    def label_from_instance(self, obj: Any) -> str:
        upcoming = obj.first_upcoming_session_at
        if upcoming is None:
            return str(obj.title)
        return f"{obj.title} ({timezone.localtime(upcoming).strftime('%b %-d, %Y')})"


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
            "wishlist",
            "donate_url",
            "essential_rules",
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
            "discord_welcome_message",
            "website_url",
            "show_members",
            "featured_class",
            "faq_label",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Guild name"}),
            "discord_webhook_url": forms.URLInput(attrs={"placeholder": "https://discord.com/api/webhooks/..."}),
            "about": forms.Textarea(
                attrs={"rows": 5, "placeholder": "Tell members what this guild is about..."},
            ),
            "wishlist": forms.Textarea(
                attrs={
                    "rows": 5,
                    "placeholder": "e.g. A pug mill, kiln shelves, underglazes. What would help this guild most?",
                },
            ),
            "donate_url": forms.URLInput(attrs={"placeholder": "https://..."}),
            "essential_rules": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": "e.g. Closed-toe shoes in the shop. No solo use of the kiln. Sign in at the front desk.",
                },
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
            "discord_welcome_message": forms.Textarea(
                attrs={"rows": 3, "placeholder": "Welcome to the guild! Here's how to get started..."},
            ),
        }
        labels = {
            "about": "About",
            "wishlist": "Wishlist",
            "donate_url": "Donate link (optional)",
            "essential_rules": "Essential / safety rules (for the flyer)",
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
            "discord_welcome_message": "Discord welcome message",
            "website_url": "Website URL",
            "show_members": "Show members roster",
            "featured_class": "Featured class",
            "faq_label": "FAQ / info section heading",
        }
        help_texts = {
            "essential_rules": "Shown on your printable flyer. Keep it to a few short lines.",
            "banner_image": "Shown at the top of the guild page. Max 5 MB.",
            "faq_label": "The heading shown above this guild's FAQ / info section — e.g. 'Ceramics Info'. Defaults to 'FAQ'.",
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
            "discord_welcome_message": (
                "Posted to your guild's Discord channel and sent to the member when someone starts "
                "following your guild via /join-guild. Blank = a generic welcome."
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

    def clean_faq_label(self) -> str:
        # A blank label falls back to the default heading rather than an empty string,
        # so the FAQ section always has a visible title.
        return (self.cleaned_data.get("faq_label") or "").strip() or "FAQ"

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

        # Blank is accepted and coerced back to "FAQ" in clean_faq_label, so leaving it
        # empty never blocks a save of the rest of the form.
        self.fields["faq_label"].required = False

        featured = cast(forms.ModelChoiceField, self.fields["featured_class"])
        if self.instance and self.instance.pk:
            # Only runs a member could still sign up for; a saved pick stays valid so
            # the rest of the form never blocks on a stale spotlight.
            pks = set(
                ClassOffering.objects.bookable().filter(category__guild=self.instance).values_list("pk", flat=True)
            )
            if self.instance.featured_class_id:
                pks.add(self.instance.featured_class_id)
            queryset = ClassOffering.objects.filter(pk__in=pks).order_by("title")
        else:
            queryset = ClassOffering.objects.none()
        self.fields["featured_class"] = _FeaturedClassChoiceField(
            queryset=queryset, required=False, label=featured.label, help_text=featured.help_text
        )

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

    # Yes/No radio over the boolean model field. Not required and coerced with
    # ``empty_value=False`` so a POST without the field (older clients, tests) means "No".
    marketing_opt_in = forms.TypedChoiceField(
        required=False,
        coerce=lambda value: value == "True",
        # django-stubs types empty_value as str | None, but TypedChoiceField accepts any
        # sentinel at runtime — False keeps cleaned_data a plain bool.
        empty_value=False,  # type: ignore[arg-type]
        choices=(
            (
                True,
                "Yes, please contact me about Past Lives Makerspace marketing opportunities to "
                "highlight my art/business (Instagram, website, email newsletter, etc.).",
            ),
            (False, "No thanks."),
        ),
        widget=forms.RadioSelect,
        label="Marketing opportunities",
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Snapshot the photo the member already has so an invalid upload can be discarded
        # without saving it (see ``save_keeping_existing_photo``).
        self._initial_photo = self.instance.profile_photo if self.instance and self.instance.pk else None
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

    def _apply_directory_fields(self, member: Member) -> None:
        """Force-list privileged roles and fold the visibility toggles into JSON."""
        if member.must_be_listed_in_directory:
            member.show_in_directory = True
        member.directory_visibility = {
            field_name: bool(self.cleaned_data.get(f"{self.VISIBILITY_PREFIX}{field_name}", True))
            for field_name in Member.DIRECTORY_TOGGLEABLE_FIELDS
        }

    def save(self, commit: bool = True) -> Member:
        member = super().save(commit=False)
        self._apply_directory_fields(member)
        if commit:
            member.save()
        return member

    @property
    def has_only_photo_errors(self) -> bool:
        """True when every validation error is confined to the profile photo field.

        Lets the view persist the member's text + visibility edits even though the new
        photo was rejected (too large / not an image) — losing those edits was the bug.
        """
        return bool(self.errors) and set(self.errors) <= {"profile_photo"}

    @property
    def photo_error(self) -> str:
        """The first profile-photo error message, for the 'photo not saved' notice."""
        return str(self.errors["profile_photo"][0])

    def save_keeping_existing_photo(self) -> Member:
        """Persist the text + visibility edits while discarding an invalid photo upload.

        Only called when :attr:`has_only_photo_errors` — the text fields are already
        applied to ``self.instance`` by the form's clean pass, so we restore the member's
        existing photo (dropping the rejected upload) and save. ``save()`` can't be used
        because the form did not fully validate.
        """
        member = self.instance
        member.profile_photo = self._initial_photo
        self._apply_directory_fields(member)
        member.save()
        return member

    class Meta:
        model = Member
        fields = [
            "preferred_name",
            "pronouns",
            "phone",
            "discord_handle",
            "about_me",
            "profile_photo",
            "show_in_directory",
            "show_on_space_map",
            "open_for_commissions",
            "commission_note",
            "marketing_opt_in",
            "instructor_bio",
        ]
        widgets = {
            "preferred_name": forms.TextInput(attrs={"placeholder": "What should we call you?"}),
            "pronouns": forms.TextInput(attrs={"placeholder": "e.g. she/her, they/them"}),
            "phone": forms.TextInput(attrs={"placeholder": "(optional)"}),
            "discord_handle": forms.TextInput(attrs={"placeholder": "@username"}),
            "about_me": forms.Textarea(attrs={"rows": 3, "placeholder": "Tell other members a bit about yourself..."}),
            "commission_note": forms.Textarea(
                attrs={
                    "rows": 2,
                    "placeholder": "e.g. Small custom woodworking, websites, AI consulting — happy to chat!",
                }
            ),
            "instructor_bio": forms.Textarea(
                attrs={"rows": 4, "placeholder": "What you teach, your background, how you like to run a class..."}
            ),
        }
        labels = {
            "show_in_directory": "Show me in the member directory",
            "show_on_space_map": "Show me on the Spaces map",
            "discord_handle": "Discord",
            "about_me": "About me",
            "profile_photo": "Profile photo",
            "open_for_commissions": "Open for commissions",
            "commission_note": "What kind of work do you welcome?",
            "instructor_bio": "About me as an instructor",
        }
        help_texts = {
            "profile_photo": "Optional. Shown next to your name in the member directory. Max 5 MB.",
            "instructor_bio": "Shown on your public instructor page.",
        }


class MemberContactForm(forms.ModelForm):
    """A single labeled contact row on the profile settings page (mirrors ``GuildLinkForm``)."""

    class Meta:
        model = MemberContact
        fields = ["label", "value", "show_in_directory", "show_on_instructor_page", "sort_order", "kind"]
        widgets = {
            "label": forms.TextInput(attrs={"placeholder": "e.g. Website, Instagram, Booking email"}),
            "value": forms.TextInput(attrs={"placeholder": "https://…, @handle, or you@example.com"}),
            "sort_order": forms.HiddenInput(),
            "kind": forms.HiddenInput(),
        }
        labels = {
            "show_in_directory": "Show in member directory",
            "show_on_instructor_page": "Show on instructor page",
        }

    def has_changed(self) -> bool:
        """Ignore a kind-only change so an untouched "+ Add" row never blocks the save.

        Each section's add-button stamps the cloned row's hidden ``kind`` the moment the
        row is created, which would otherwise make an abandoned blank row count as
        "changed" and fail required-field validation on save.
        """
        return bool(set(self.changed_data) - {"kind"})


MemberContactFormSet = forms.inlineformset_factory(
    Member, MemberContact, form=MemberContactForm, extra=0, can_delete=True
)


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


class DeleteAccountConfirmForm(forms.Form):
    """Requires typing DELETE exactly to confirm irreversible self-service deletion."""

    CONFIRM_TEXT = "DELETE"

    confirm_text = forms.CharField(
        label="Type DELETE to confirm",
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )

    def clean_confirm_text(self) -> str:
        value = self.cleaned_data["confirm_text"].strip()
        if value != self.CONFIRM_TEXT:
            raise forms.ValidationError("Type DELETE (all capitals) to confirm.")
        return value


class NotificationEmailForm(forms.Form):
    """Pick which verified address event-driven notification emails go to.

    Choices are built per-user in ``__init__`` from the user's VERIFIED allauth
    ``EmailAddress`` rows plus a blank "Primary email (default)" option, so an
    unverified or foreign address can never validate. This form is the only write
    path for ``Member.notification_email`` — the model field itself stays a plain
    ``EmailField`` (validation lives here, per the house rule).
    """

    notification_email = forms.ChoiceField(
        required=False,
        label="Send notifications to",
    )

    def __init__(self, user: User, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.user = user
        from allauth.account.models import EmailAddress

        verified = EmailAddress.objects.filter(user=user, verified=True).order_by("email")
        cast(forms.ChoiceField, self.fields["notification_email"]).choices = [("", "Primary email (default)")] + [
            (ea.email, ea.email) for ea in verified
        ]

    def save(self) -> None:
        """Write the chosen address onto the user's member ("" = follow the primary)."""
        member = self.user.member
        member.notification_email = self.cleaned_data["notification_email"]
        member.save(update_fields=["notification_email"])


class GuildUpdatesPromptForm(forms.Form):
    """Validates the first-login guild updates picks (active guild pks only).

    Validation only — the template renders the toggle rows itself (service-built grid,
    same as the notifications matrix and the settings Guilds tab), so the field's
    widget is a hidden multi-select rather than a rendered control. An inactive or
    bogus pk fails with a single plain message; real members can't reach that state
    from the UI.
    """

    guilds = forms.ModelMultipleChoiceField(
        queryset=Guild.objects.filter(is_active=True),
        required=False,
        widget=forms.MultipleHiddenInput,
        error_messages={
            "invalid_choice": "Pick guilds from the list.",
            "invalid_pk_value": "Pick guilds from the list.",
            "invalid_list": "Pick guilds from the list.",
        },
    )


class BetaFeedbackForm(forms.Form):
    """Form for submitting feedback (bug reports, feature requests, general feedback)."""

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
        subject = f"[{category_label}] {self.cleaned_data['subject']}"
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
        (Member.ADMIN_ROLE_GUEST, "Guest"),
    ]

    role = forms.ChoiceField(
        choices=ROLE_CHOICES,
        label="Role",
        help_text=(
            "Admin / Guild Officer / Member set the hierarchy role. "
            "Guest deactivates the member (no hub access). "
            "Instructor is now a permission on the Permissions tab, not a role."
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
            "can_self_approve_discounts",
        ]
        widgets = {
            "pronouns": forms.TextInput(attrs={"placeholder": "e.g. she/her, they/them"}),
        }
        labels = {
            "can_self_approve_discounts": "Can approve their own discount codes",
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["role"].initial = self._derive_initial_role(self.instance)

    @staticmethod
    def _derive_initial_role(member: Member) -> str:
        # Instructor is a permission now (Permissions tab), not a role — an instructor
        # shows as their underlying hierarchy role here.
        if member.status != Member.Status.ACTIVE:
            return Member.ADMIN_ROLE_GUEST
        return member.fog_role


class MemberCapabilitiesForm(forms.Form):
    """A member's scoped, site-wide admin duties, one BooleanField per capability.

    Each field renders as a labeled toggle (``components/toggle.html``) on the member
    edit Permissions tab. A capability is the master switch: holding it usually both
    routes the matching approval/alert emails to this member AND lets them act on that
    object type, without granting full admin. Two exceptions: Refunds is action-only
    (routes nothing), and Billing Administrator additionally gates the admin Payments
    dashboard. These are SITE-WIDE — per-guild lead/staff authority is managed on the
    guild's own Staff tab, not here.

    Build for GET with ``MemberCapabilitiesForm(initial=MemberCapabilitiesForm.initial_for(member))``;
    on POST, ``form.selected()`` returns the checked capability values for
    :meth:`membership.models.Member.sync_admin_capabilities`.
    """

    cap_class_approver = forms.BooleanField(
        required=False,
        label="CMS Administrator",
        help_text="Approves and publishes classes for every guild, and gets class-review emails.",
    )
    cap_space_approver = forms.BooleanField(
        required=False,
        label="Space & Cubby Administrator",
        help_text="Reviews space and cubby requests, and gets those request emails.",
    )
    cap_discount_approver = forms.BooleanField(
        required=False,
        label="Discount Code Administrator",
        help_text="Approves discount codes, and gets discount-request emails.",
    )
    cap_events_approver = forms.BooleanField(
        required=False,
        label="Calendar Administrator",
        help_text="Reviews Community Calendar and meeting proposals, and gets those emails.",
    )
    cap_billing_approver = forms.BooleanField(
        required=False,
        label="Billing Administrator",
        help_text="Sees the admin Payments dashboard and gets an alert when a member's automatic payment fails.",
    )
    cap_refunds = forms.BooleanField(
        required=False,
        label="Refunds",
        help_text=(
            "Can send Stripe refunds for class and orientation payments. "
            "Adds Refund buttons on payment pages this member can already reach. "
            "It does not open any new pages, so pair it with Billing Administrator "
            "for the Payments panel."
        ),
    )

    # Field name → the capability it grants. The single source of truth both
    # ``initial_for`` and ``selected`` read, so the two never drift.
    _FIELD_TO_CAP: dict[str, str] = {
        "cap_class_approver": AdminCapability.Capability.CLASS_APPROVER,
        "cap_space_approver": AdminCapability.Capability.SPACE_APPROVER,
        "cap_discount_approver": AdminCapability.Capability.DISCOUNT_APPROVER,
        "cap_events_approver": AdminCapability.Capability.EVENTS_APPROVER,
        "cap_billing_approver": AdminCapability.Capability.BILLING_APPROVER,
        "cap_refunds": AdminCapability.Capability.REFUNDS,
    }

    @classmethod
    def initial_for(cls, member: Member) -> dict[str, bool]:
        """The checked-state map for ``member``'s currently held capabilities."""
        held = set(member.admin_capabilities.values_list("capability", flat=True))
        return {name: cap in held for name, cap in cls._FIELD_TO_CAP.items()}

    def selected(self) -> list[str]:
        """The capability values whose toggles are checked (call after ``is_valid``)."""
        return [cap for name, cap in self._FIELD_TO_CAP.items() if self.cleaned_data.get(name)]


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
            "instructor_discount_codes_enabled",
            "mailchimp_api_key",
            "mailchimp_list_id",
            "google_analytics_measurement_id",
            "discord_general_webhook_url",
            "discord_leadership_webhook_url",
            "discord_officers_webhook_url",
            "discord_server_id",
            "discord_role_message_channel_id",
            "discord_role_message_id",
            "my_tab_enabled",
            "class_registration_enabled",
            "class_registration_disabled_note",
            "help_page_enabled",
            "wiki_link_enabled",
            "guild_welcome_email_enabled",
            "display_demo_classes",
            "display_demo_guild",
            "member_directory_public",
            "member_event_policy",
            "member_google_calendar_id",
            "public_google_calendar_id",
            "google_calendar_sync_enabled",
            "discord_events_sync_enabled",
            "discord_calendar_channel_id",
            "discord_calendar_posts_enabled",
            "discord_classes_channel_id",
            "discord_classes_posts_enabled",
            "discord_info_channel_id",
            "discord_info_message_id",
            "discord_info_links_content",
            "signage_default_slide_seconds",
            "signage_show_events",
            "signage_event_days_ahead",
        ]
        widgets = {
            "classes_calendar_color": forms.TextInput(attrs={"type": "color"}),
            "class_registration_disabled_note": forms.Textarea(attrs={"rows": 3}),
            "member_google_calendar_id": forms.TextInput(attrs={"placeholder": "abc123@group.calendar.google.com"}),
            "public_google_calendar_id": forms.TextInput(attrs={"placeholder": "abc123@group.calendar.google.com"}),
            "discord_info_links_content": forms.Textarea(attrs={"rows": 14}),
        }

    def clean_discord_info_links_content(self) -> str:
        """Cap the links copy at Discord's embed-description limit before it can 400 a sync."""
        from hub.discord_info_post import EMBED_DESCRIPTION_MAX

        content: str = self.cleaned_data["discord_info_links_content"]
        if len(content) > EMBED_DESCRIPTION_MAX:
            raise forms.ValidationError(
                f"Keep the links content under {EMBED_DESCRIPTION_MAX:,} characters (Discord's embed limit)."
            )
        return content


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


# Site Settings → Automations tab. One ``enabled`` toggle per scheduled job, saved by the
# page's Save. The row set is fixed by the code registry (not user-managed), so ``extra=0``
# and no add/delete — admins pause or run jobs, they don't add or remove them.
ScheduledJobStateFormSet: type[forms.BaseModelFormSet] = forms.modelformset_factory(
    ScheduledJobState,
    fields=["enabled"],
    extra=0,
)


class DiscordGuildEmojiForm(forms.ModelForm):
    """One row in the Site Settings → Discord tab's emoji → guild map (D2)."""

    class Meta:
        model = DiscordGuildEmoji
        fields = ["emoji", "guild"]
        widgets = {
            "emoji": forms.TextInput(attrs={"placeholder": "🔥 or name:id"}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Only active guilds are pickable (the map drives live joins).
        guild_field = cast(forms.ModelChoiceField, self.fields["guild"])
        guild_field.queryset = Guild.objects.filter(is_active=True).order_by("name")


DiscordGuildEmojiFormSet = forms.modelformset_factory(
    DiscordGuildEmoji,
    form=DiscordGuildEmojiForm,
    extra=0,
    can_delete=True,
)


class GuildRoleForm(forms.ModelForm):
    """One active guild's canonical outbound Discord role id(s) (D3).

    The model field is a JSON list (Glass keeps two roles in lockstep); this form
    presents it as a single space/comma-separated text input and parses it back.
    """

    discord_role_ids = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "e.g. 123456789012345678"}),
        help_text=(
            "Discord role id(s) assigned/removed when a member joins/leaves in-app. Separate "
            "multiple with commas or spaces (most guilds have one; a collapsed guild like Glass has "
            "two). Blank disables outbound role sync for this guild."
        ),
    )

    class Meta:
        model = Guild
        fields: list[str] = []  # ``discord_role_ids`` is handled as a declared field below.

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["discord_role_ids"].initial = " ".join(self.instance.discord_role_ids or [])

    def clean_discord_role_ids(self) -> list[str]:
        import re

        raw = self.cleaned_data.get("discord_role_ids", "") or ""
        ids = [token for token in re.split(r"[,\s]+", raw.strip()) if token]
        for token in ids:
            if not token.isdigit():
                raise forms.ValidationError(f"'{token}' is not a valid Discord role id (digits only).")
        return ids

    def save(self, commit: bool = True) -> Guild:
        self.instance.discord_role_ids = self.cleaned_data["discord_role_ids"]
        return cast(Guild, super().save(commit=commit))


GuildRoleFormSet = forms.modelformset_factory(
    Guild,
    form=GuildRoleForm,
    extra=0,
    can_delete=False,
)


class SlideshowZoneForm(forms.ModelForm):
    """One row in the Slideshow tab's Zones editor — one physical screen location."""

    class Meta:
        model = SlideshowZone
        fields = ["name", "slug", "is_enabled", "sort_order"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. Woodshop"}),
            "slug": forms.TextInput(attrs={"placeholder": "woodshop"}),
        }
        help_texts = {
            "slug": "Used in the screen URL: slideshow.pastlives.space/<slug>/. Leave blank to auto-fill from the name.",
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # The slug auto-fills from the name (below) when left blank, so it isn't required.
        self.fields["slug"].required = False

    def clean(self) -> dict[str, Any]:
        cleaned = cast(dict[str, Any], super().clean())
        if cleaned.get("DELETE"):
            return cleaned
        if cleaned.get("name") and not cleaned.get("slug"):
            cleaned["slug"] = slugify(cleaned["name"])
        return cleaned


SlideshowZoneFormSet = forms.modelformset_factory(
    SlideshowZone,
    form=SlideshowZoneForm,
    extra=0,
    can_delete=True,
)


class SlideshowSlideForm(forms.ModelForm):
    """One row in the Slideshow tab's Slides editor.

    A row is a custom slide OR a mirror of a published guild announcement — the ``kind``
    select toggles which fields apply (Alpine ``x-model`` in the template). Because only
    an admin reaches this tab, the announcement picker is the privacy-safe, admin-curated
    opt-in the design requires.
    """

    announcement = forms.ModelChoiceField(
        queryset=GuildAnnouncement.objects.published(),
        required=False,
        empty_label="— choose an announcement —",
        help_text="Pick a published announcement to mirror. Only used for 'Guild announcement' slides.",
    )
    # Themed "Remove image" button drives this hidden flag (Django's raw Clear checkbox
    # is suppressed by using a plain FileInput widget). See save().
    remove_image = forms.BooleanField(required=False, widget=forms.HiddenInput())

    class Meta:
        model = SlideshowSlide
        fields = [
            "kind",
            "zone",
            "title",
            "body",
            "image",
            "link_url",
            "show_qr",
            "announcement",
            "starts_on",
            "ends_on",
            "is_enabled",
            "sort_order",
        ]
        widgets = {
            "kind": forms.Select(attrs={"x-model": "kind"}),
            "body": forms.Textarea(attrs={"rows": 3}),
            "image": forms.FileInput(),
            # Drag-to-reorder (grip handle / move buttons) rewrites this hidden value to the
            # row's visual index; "Save slides" persists it. Never a visible number input.
            "sort_order": forms.HiddenInput(),
            "starts_on": forms.DateInput(
                attrs={"type": "date", "@click": "try { $event.currentTarget.showPicker() } catch (e) {}"}
            ),
            "ends_on": forms.DateInput(
                attrs={"type": "date", "@click": "try { $event.currentTarget.showPicker() } catch (e) {}"}
            ),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["zone"].empty_label = "All screens"  # type: ignore[attr-defined]

    def clean(self) -> dict[str, Any]:
        cleaned = cast(dict[str, Any], super().clean())
        if cleaned.get("DELETE"):
            return cleaned
        has_saved_image = bool(self.instance.pk and self.instance.image)
        has_content = bool(
            cleaned.get("title")
            or cleaned.get("body")
            or cleaned.get("image")
            or cleaned.get("link_url")
            or cleaned.get("announcement")
            or has_saved_image
        )
        if not has_content:
            # An untouched +Add row — don't block the save with a requirement error.
            return cleaned
        if cleaned.get("kind") == SlideshowSlide.Kind.ANNOUNCEMENT:
            if not cleaned.get("announcement"):
                raise forms.ValidationError("Pick an announcement for this slide.")
        elif not cleaned.get("title") and not cleaned.get("image") and not has_saved_image:
            raise forms.ValidationError("Give the slide a title or an image.")
        return cleaned

    def save(self, commit: bool = True) -> SlideshowSlide:
        instance = cast(SlideshowSlide, super().save(commit=False))
        # Honor the "Remove image" button unless a new file was uploaded in the same submit.
        # A plain FileInput keeps the existing file in cleaned_data, so detect a genuine new
        # upload via request.FILES rather than the (kept-or-new) cleaned value.
        new_upload = self.add_prefix("image") in self.files
        if self.cleaned_data.get("remove_image") and not new_upload:
            instance.image = None
        if commit:
            instance.save()
        return instance


SlideshowSlideFormSet = forms.modelformset_factory(
    SlideshowSlide,
    form=SlideshowSlideForm,
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
        """Validate the ranked ballot.

        All three choices are required (policy: every ballot assigns 5, 3, and 2
        points), and they must be distinct guilds.
        """
        cleaned: dict = super().clean() or {}
        g1 = cleaned.get("guild_1st")
        g2 = cleaned.get("guild_2nd")
        g3 = cleaned.get("guild_3rd")

        chosen = [g for g in (g1, g2, g3) if g]
        if len({g.pk for g in chosen}) != len(chosen):
            raise forms.ValidationError("Each choice must be a different guild.")

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
        help_texts = {
            "answer": "You can use Markdown — **bold**, lists, and [links](https://example.com) all render on the page.",
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


class GuildMailingListEmailForm(forms.ModelForm):
    """A single custom (non-member) mailing-list address row on the guild edit page."""

    class Meta:
        model = GuildMailingListEmail
        fields = ["email", "label", "sort_order"]
        widgets = {"sort_order": forms.HiddenInput()}


GuildMailingListFormSet = forms.inlineformset_factory(
    Guild, GuildMailingListEmail, form=GuildMailingListEmailForm, extra=0, can_delete=True
)


class OrgInfoPageForm(forms.ModelForm):
    """Main edit form for the Space & Org Info page — rich-text sections + the two images.

    The text blocks are dual-mode fields: legacy values are Markdown, but the editor is a
    Quill rich-text editor (``PageContentEditorWidget``) and every save goes through
    ``sanitize_page_submission`` — editor HTML is sanitized to the page-content allowlist,
    a non-HTML value (no-JS fallback) passes through and keeps rendering as Markdown. The
    FAQ and Links save via their own endpoints, exactly like the guild editor.
    """

    class Meta:
        model = OrgInfoPage
        fields = [
            "intro",
            "floorplan_caption",
            "parking",
            "who_to_contact",
            "code_of_conduct",
            "code_of_conduct_url",
            "banner_image",
            "floorplan_image",
        ]
        widgets = {
            "intro": PageContentEditorWidget(attrs={"rows": 4}),
            "floorplan_caption": forms.TextInput(
                attrs={"placeholder": "Guild locations, restrooms, and emergency exits."}
            ),
            "parking": PageContentEditorWidget(attrs={"rows": 4}),
            "who_to_contact": PageContentEditorWidget(attrs={"rows": 6}),
            "code_of_conduct": PageContentEditorWidget(attrs={"rows": 8}),
            "code_of_conduct_url": forms.URLInput(attrs={"placeholder": "https://docs.google.com/…"}),
        }
        labels = {
            "intro": "Intro / welcome",
            "floorplan_caption": "Map caption",
            "parking": "Parking & arrival",
            "who_to_contact": "Who's who / who to contact",
            "code_of_conduct": "Code of conduct",
            "code_of_conduct_url": "…or a code-of-conduct link",
            "banner_image": "Banner image",
            "floorplan_image": "Floor plan / map",
        }
        help_texts = {
            "intro": "Use the toolbar to format — bold, lists, and links.",
            "parking": "Use the toolbar to format.",
            "who_to_contact": "Use the toolbar to format — a list of 'topic → who to contact' works well.",
            "code_of_conduct": "Use the toolbar to format. Leave blank to link out with the field below instead.",
            "code_of_conduct_url": "Used only when the body above is blank.",
        }

    def clean_intro(self) -> str:
        return sanitize_page_submission(self.cleaned_data.get("intro") or "")

    def clean_parking(self) -> str:
        return sanitize_page_submission(self.cleaned_data.get("parking") or "")

    def clean_who_to_contact(self) -> str:
        return sanitize_page_submission(self.cleaned_data.get("who_to_contact") or "")

    def clean_code_of_conduct(self) -> str:
        return sanitize_page_submission(self.cleaned_data.get("code_of_conduct") or "")


class OrgFAQItemForm(forms.ModelForm):
    """A single FAQ question/answer row on the Space & Org Info editor — mirrors ``GuildFAQItemForm``.

    The answer is a dual-mode rich-text field (see ``OrgInfoPageForm``); the guild FAQ
    form stays plain Markdown — only the admin help-center editor moved to rich text.
    """

    class Meta:
        model = OrgFAQItem
        fields = ["question", "answer", "video_url", "document", "document_url", "sort_order"]
        widgets = {
            # Org FAQ answers historically rendered through the *member* Markdown profile —
            # the widget's markdown_profile keeps a legacy answer displaying identically.
            "answer": PageContentEditorWidget(attrs={"rows": 3}, markdown_profile="member"),
            "video_url": forms.URLInput(attrs={"placeholder": "https://youtube.com/watch?v=…"}),
            "document_url": forms.URLInput(attrs={"placeholder": "https://docs.google.com/…"}),
            "sort_order": forms.HiddenInput(),
        }
        labels = {
            "video_url": "Video (YouTube)",
            "document": "Document (upload)",
            "document_url": "…or document link",
        }
        help_texts = {
            "answer": "Use the toolbar to format — bold, lists, and links all render on the page.",
        }

    def clean_answer(self) -> str:
        """Sanitize a rich-editor answer; reject one that sanitizes to nothing (e.g. ``<p><br></p>``)."""
        answer = sanitize_page_submission(self.cleaned_data["answer"])
        if not answer:
            raise forms.ValidationError("Add an answer.")
        return answer

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
        if cleaned.get("DELETE"):
            return cleaned
        if cleaned.get("document") and cleaned.get("document_url"):
            raise forms.ValidationError("Add a document OR a link for this answer, not both.")
        return cleaned


OrgFAQItemFormSet = forms.inlineformset_factory(OrgInfoPage, OrgFAQItem, form=OrgFAQItemForm, extra=0, can_delete=True)


class OrgLinkForm(forms.ModelForm):
    """A single external-link row on the Space & Org Info editor — mirrors ``GuildLinkForm``."""

    class Meta:
        model = OrgLink
        fields = ["label", "url", "sort_order"]
        widgets = {"sort_order": forms.HiddenInput()}


OrgLinkFormSet = forms.inlineformset_factory(OrgInfoPage, OrgLink, form=OrgLinkForm, extra=0, can_delete=True)


RESERVED_HELP_SLUGS = frozenset({"edit", "search", "categories", "articles", "faq", "links", "floorplan", "more"})
"""Slugs that collide with the fixed /help/… routes — a category can never claim one."""


class HelpCategoryForm(forms.ModelForm):
    """A single help-center category row in the editor — mirrors ``OrgFAQItemForm``."""

    class Meta:
        model = HelpCategory
        fields = ["name", "slug", "audience", "description", "sort_order"]
        widgets = {"sort_order": forms.HiddenInput()}
        help_texts = {
            "slug": "Optional — the /help/ URL segment; auto-filled from the name.",
        }

    def clean_slug(self) -> str:
        """Reject slugs that collide with the fixed /help/… routes."""
        slug = cast(str, self.cleaned_data["slug"])
        if slug in RESERVED_HELP_SLUGS:
            raise forms.ValidationError("That name is reserved — pick another.")
        return slug


HelpCategoryFormSet = forms.modelformset_factory(HelpCategory, form=HelpCategoryForm, extra=0, can_delete=True)


class WikiArticleForm(forms.ModelForm):
    """A single Wiki article row in the editor — mirrors ``OrgFAQItemForm``."""

    category = forms.ModelChoiceField(
        queryset=HelpCategory.objects.all(),
        required=False,
        empty_label="— No category (hidden from the landing grid) —",
    )
    related_articles = forms.ModelMultipleChoiceField(
        queryset=WikiArticle.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={"size": 6}),
        help_text=(
            "Ctrl/Cmd-click to pick a few — shown under 'Related guides'. "
            "Same-category guides fill the rest automatically."
        ),
    )

    class Meta:
        model = WikiArticle
        fields = ["title", "slug", "category", "body", "related_articles", "sort_order", "is_published"]
        widgets = {
            "sort_order": forms.HiddenInput(),
            "body": PageContentEditorWidget(attrs={"rows": 10}),
        }
        help_texts = {
            "slug": "Optional. The #anchor for deep links (e.g. /help/#guild-voting). Leave blank to fill it from the title.",
            "body": (
                "Use the toolbar to format — bold, headings, lists, and links all render on the page. "
                "A guide originally written in Markdown opens here as formatted text and saves as rich "
                "text from then on."
            ),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """An article can't relate to itself — drop the row being edited from the picker."""
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["related_articles"].queryset = WikiArticle.objects.exclude(  # type: ignore[attr-defined]
                pk=self.instance.pk
            )

    def clean_body(self) -> str:
        """Sanitize a rich-editor body; reject one that sanitizes to nothing (e.g. ``<p><br></p>``)."""
        body = sanitize_page_submission(self.cleaned_data["body"])
        if not body:
            raise forms.ValidationError("The guide needs a body.")
        return body


WikiArticleFormSet = forms.inlineformset_factory(
    OrgInfoPage, WikiArticle, form=WikiArticleForm, extra=0, can_delete=True
)


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


class MeetingCreateForm(forms.Form):
    """The '+ New meeting' modal (Meetings spec §6.2): For scope + Type, both per-user.

    The For choices are permission-derived — the guilds this user can edit, plus
    Council for everyone who passes :func:`~membership.permissions.can_edit_meeting`
    for the council scope (admins AND any guild's lead/staff). A posted scope outside
    the list is therefore a permission failure, which the create view returns as 403.
    """

    KIND_CHOICES = [("monthly", "Monthly"), ("special", "Special")]

    scope = forms.ChoiceField(label="For", choices=[])
    kind = forms.ChoiceField(label="Type", choices=KIND_CHOICES, initial="monthly")

    def __init__(self, *args: Any, request: HttpRequest, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        from membership.permissions import editable_meeting_scopes

        guilds, council = editable_meeting_scopes(request)
        choices: list[tuple[str, str]] = [(str(guild.pk), guild.name) for guild in guilds]
        if council:
            choices.append(("council", "Council"))
        cast(forms.ChoiceField, self.fields["scope"]).choices = choices

    def scope_guild(self) -> Guild | None:
        """The chosen scope as a Guild, or ``None`` for the council. Valid forms only."""
        value = self.cleaned_data["scope"]
        if value == "council":
            return None
        return Guild.objects.get(pk=int(value))


class MeetingItemProposalForm(forms.Form):
    """The 'Propose an agenda item' modal (Meetings spec §6.3): Topic + optional Why."""

    title = forms.CharField(label="Topic", max_length=200)
    why = forms.CharField(
        label="Why / what needs deciding",
        required=False,
        help_text="Helps leadership slot it into the meeting.",
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class MeetingProposalDecisionForm(forms.Form):
    """The reviewer decision modal POST (Meetings spec §5.4/§6.3).

    Approve is the edit-then-approve path — the possibly-tweaked Title/Why land on the
    created agenda item, so a topic is required. Decline carries only an optional note
    back to the proposer (the locked decision — unlike event declines).
    """

    DECISION_CHOICES = [("approve", "Approve"), ("decline", "Decline")]

    decision = forms.ChoiceField(choices=DECISION_CHOICES)
    title = forms.CharField(max_length=200, required=False)
    why = forms.CharField(required=False, widget=forms.Textarea)
    note = forms.CharField(required=False, widget=forms.Textarea)

    def clean(self) -> dict[str, Any]:
        cleaned = cast(dict[str, Any], super().clean())
        if cleaned.get("decision") == "approve" and not cleaned.get("title"):
            raise forms.ValidationError("Give the agenda item a topic.")
        return cleaned


class MeetingLockDispositionForm(forms.Form):
    """The lock-time disposition modal POST (Meetings spec §6.3) — one Carry / Set-aside
    choice per still-pending proposal. ``Meeting.approve()`` owns the default-to-carry
    rule; this form only parses / validates what WAS submitted."""

    def __init__(self, *args: Any, proposal_ids: list[int], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._proposal_ids = proposal_ids
        for pk in proposal_ids:
            self.fields[f"disposition_{pk}"] = forms.ChoiceField(
                choices=MeetingItemProposal.Disposition.choices, required=False
            )

    def dispositions(self) -> dict[int, str]:
        """Cleaned per-proposal map for ``Meeting.approve(dispositions=...)``. A proposal
        whose radio wasn't submitted (or whose field didn't exist at render time) is
        omitted, which ``approve()`` reads as default-to-carry."""
        return {pk: value for pk in self._proposal_ids if (value := self.cleaned_data[f"disposition_{pk}"])}


class MeetingAttachmentForm(forms.ModelForm):
    """The workspace's '+ Attach file or link' modal — exactly one of file / url.

    Copies the meeting-note attachment XOR idiom (the user-facing guard in front of
    the model's ``ck_meetingattachment_file_xor_url`` constraint).
    """

    class Meta:
        model = MeetingAttachment
        fields = ["label", "file", "url"]

    def clean(self) -> dict[str, Any]:
        cleaned = cast(dict[str, Any], super().clean())
        has_file = bool(cleaned.get("file"))
        has_url = bool(cleaned.get("url"))
        if has_file == has_url:  # both empty or both filled
            raise forms.ValidationError("Pick a file or paste a link, not both.")
        return cleaned


class GuildOrientationSettingsForm(forms.ModelForm):
    """Edit a guild's guild-wide orientation switches.

    The lead-authored thank-you email lives on its own :class:`GuildThankyouEmailForm`
    (also on the Orientations tab). Per-orientation config — duration, price, seats,
    location — is edited per type on :class:`OrientationTypeFormSet`, not here.
    """

    class Meta:
        model = GuildOrientationSettings
        fields = [
            "is_enabled",
            "allow_custom_requests",
            "info",
            "is_closed",
            "closed_message",
        ]
        widgets = {
            "info": forms.Textarea(attrs={"rows": 4}),
            "closed_message": forms.TextInput(attrs={"placeholder": "On vacation till Sept 8"}),
        }
        labels = {
            "is_enabled": "Offer orientation booking on this guild's page",
            "allow_custom_requests": "Let members propose their own orientation time",
            "info": "Orientation info",
            "is_closed": "Temporarily closed for orientations",
            "closed_message": "Closed message",
        }


class OrientationTypeForm(forms.ModelForm):
    """One row of the guild editor's Orientation Types list.

    ``price`` is entered in dollars ("15" or "15.50", never cents) and mapped to
    ``price_cents`` on save. Blank normalizes to 0 (free), and a free type renders
    the field empty, not "0". Price changes affect future checkouts only — live
    holds and paid bookings keep the amount they paid.
    """

    price = forms.DecimalField(
        max_digits=6,
        decimal_places=2,
        required=False,
        label="Price",
        widget=forms.NumberInput(attrs={"placeholder": "Free", "min": "0", "step": "0.01"}),
    )

    class Meta:
        model = OrientationType
        fields = [
            "name",
            "description",
            "duration_minutes",
            "default_seats",
            "default_location",
            "sort_order",
            "is_active",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Shop Basics"}),
            "description": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {
            "name": "Name",
            "description": "Description (shown to members)",
            "duration_minutes": "Length (minutes)",
            "default_seats": "Seats per slot",
            "default_location": "Location",
            "sort_order": "Sort order",
            "is_active": "Active",
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.price_cents:
            self.fields["price"].initial = Decimal(self.instance.price_cents) / 100

    def clean_price(self) -> int:
        """Normalize the dollar input to cents — blank means free."""
        price = self.cleaned_data["price"]
        if price in (None, ""):
            return 0
        if not Decimal("0") <= price <= Decimal("500"):
            raise forms.ValidationError("Enter a price between $0 and $500.")
        return int(price * 100)

    def save(self, commit: bool = True) -> OrientationType:
        instance = cast(OrientationType, super().save(commit=False))
        instance.price_cents = self.cleaned_data["price"]
        if commit:
            instance.save()
        return instance


class BaseOrientationTypeFormSet(forms.BaseInlineFormSet):
    """Guards type deletion at the FORMSET level — deleted forms skip per-form validation.

    Deleting a type would cascade-delete its slots AND its booking history, so a
    type with any booking can only be retired (the Active toggle), never deleted.
    """

    def clean(self) -> None:
        super().clean()
        for form in self.deleted_forms:
            if form.instance.pk and form.instance.bookings.exists():
                raise forms.ValidationError(
                    "This orientation has booking history and can't be deleted. Turn off Active to retire it instead."
                )


OrientationTypeFormSet = forms.inlineformset_factory(
    Guild, OrientationType, form=OrientationTypeForm, formset=BaseOrientationTypeFormSet, extra=0, can_delete=True
)


class GuildThankyouEmailForm(forms.ModelForm):
    """Edit a guild's lead-authored thank-you email.

    Lives on the Orientations tab of the guild editor — it is the orientation-lifecycle
    email, sent once an orientation is marked complete. The email *data* stays on
    :class:`~membership.models.GuildOrientationSettings`; only the editing UI lives here.
    The thank-you email is on by default and falls back to the standard copy, so enabling
    it needs no subject or body. Saving stamps ``thankyou_email_updated_at`` when a
    thank-you field changed.
    """

    class Meta:
        model = GuildOrientationSettings
        fields = [
            "thankyou_email_enabled",
            "thankyou_email_subject",
            "thankyou_email_body",
        ]
        widgets = {
            "thankyou_email_body": RichTextEditorWidget(attrs={"rows": 6}),
        }
        labels = {
            "thankyou_email_enabled": "Send a thank-you / next-steps email after orientation",
            "thankyou_email_subject": "Thank-you subject",
            "thankyou_email_body": "Thank-you message",
        }

    def clean_thankyou_email_body(self) -> str:
        return sanitize_rich_html(self.cleaned_data.get("thankyou_email_body") or "")

    _THANKYOU_EMAIL_FIELDS = ("thankyou_email_enabled", "thankyou_email_subject", "thankyou_email_body")

    def save(self, commit: bool = True) -> GuildOrientationSettings:
        if set(self.changed_data).intersection(self._THANKYOU_EMAIL_FIELDS):
            self.instance.thankyou_email_updated_at = timezone.now()
        return cast(GuildOrientationSettings, super().save(commit=commit))


class GuildWelcomeEmailForm(forms.ModelForm):
    """Edit a guild's lead-authored welcome email.

    Lives on the Welcome Email tab of the guild editor — it is the join-lifecycle email,
    sent once a member deliberately joins (the "Join This Guild" button with the welcome
    box checked, or the Discord ``/join-guild`` command). The email *data* stays on
    :class:`~membership.models.GuildOrientationSettings`; only the editing UI lives here.
    The welcome email is on by default and falls back to the standard copy, so enabling it
    needs no subject or body. Saving stamps ``welcome_email_updated_at`` when a welcome
    field changed.
    """

    class Meta:
        model = GuildOrientationSettings
        fields = [
            "welcome_email_enabled",
            "welcome_email_subject",
            "welcome_email_body",
        ]
        widgets = {
            "welcome_email_body": RichTextEditorWidget(attrs={"rows": 6}),
        }
        labels = {
            "welcome_email_enabled": "Send a welcome email when a member joins this guild",
            "welcome_email_subject": "Welcome subject",
            "welcome_email_body": "Welcome message",
        }

    def clean_welcome_email_body(self) -> str:
        return sanitize_rich_html(self.cleaned_data.get("welcome_email_body") or "")

    _WELCOME_EMAIL_FIELDS = ("welcome_email_enabled", "welcome_email_subject", "welcome_email_body")

    def save(self, commit: bool = True) -> GuildOrientationSettings:
        if set(self.changed_data).intersection(self._WELCOME_EMAIL_FIELDS):
            self.instance.welcome_email_updated_at = timezone.now()
        return cast(GuildOrientationSettings, super().save(commit=commit))


class GuildJoinForm(forms.Form):
    """The join-modal opt-ins: the welcome email plus an optional Discord announcement.

    Not persisted — it only carries the member's choices with the join POST. ``send_welcome``
    is checked by default (opt-out within the deliberate join, honoring "ask first").
    ``announce_discord`` is OFF by default (opt-in): it only posts a short celebratory message
    to the guild's own Discord channel when the member deliberately ticks it, and the toggle is
    only rendered for guilds that actually post to a channel. The view reads both off the POST.
    """

    send_welcome = forms.BooleanField(
        required=False,
        initial=True,
        label="Email me the guild's welcome email",
    )
    announce_discord = forms.BooleanField(
        required=False,
        initial=False,
        label="Announce on the guild's Discord channel",
    )


class OrientationAvailabilityForm(forms.ModelForm):
    """A single recurring orientation-availability row.

    Times are half-hour ``<select>`` dropdowns (Rule 19), not per-minute pickers; the
    "HH:MM" choice is parsed back to a ``datetime.time`` on clean, and an existing off-grid
    value is preserved via :func:`_seed_time_choice`. ``orientation_type`` is scoped to the
    guild's active types (plus the row's own type, so an existing row under a retired type
    still validates); it defaults to the guild's first active type.
    """

    start_time = forms.ChoiceField(choices=half_hour_time_choices(required=True), label="Start time")
    end_time = forms.ChoiceField(choices=half_hour_time_choices(required=True), label="End time")

    class Meta:
        model = OrientationAvailability
        fields = ["orientation_type", "weekday", "start_time", "end_time", "seats", "is_active"]
        labels = {"orientation_type": "Orientation"}

    def __init__(self, *args: Any, guild: Guild | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if guild is None and self.instance is not None and self.instance.guild_id is not None:
            guild = self.instance.guild
        type_field = cast(forms.ModelChoiceField, self.fields["orientation_type"])
        if guild is not None:
            allowed = OrientationType.objects.filter(guild=guild).active()
            if self.instance is not None and self.instance.pk and self.instance.orientation_type_id is not None:
                allowed = allowed | OrientationType.objects.filter(pk=self.instance.orientation_type_id)
            type_field.queryset = allowed.distinct()
            first_type = guild.first_active_orientation_type()
            if first_type is not None:
                type_field.initial = first_type.pk
        type_field.empty_label = None
        type_field.error_messages["invalid_choice"] = "Pick one of this guild's orientations."
        if self.instance and self.instance.pk:
            _seed_time_choice(self, "start_time", self.instance.start_time)
            _seed_time_choice(self, "end_time", self.instance.end_time)

    def clean_start_time(self) -> time:
        return _parse_time_choice(self.cleaned_data["start_time"])

    def clean_end_time(self) -> time:
        return _parse_time_choice(self.cleaned_data["end_time"])

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


_SLOT_DURATION_CHOICES: list[tuple[str, str]] = [
    ("30", "30 minutes"),
    ("45", "45 minutes"),
    ("60", "1 hour"),
    ("90", "1.5 hours"),
    ("120", "2 hours"),
    ("180", "3 hours"),
]


class OrientationSlotForm(forms.ModelForm):
    """Add a one-off orientation slot from the Upcoming Slots card.

    First surfaced with per-orienter availability: date + half-hour start + duration
    dropdowns (Rule 20 — no per-minute pickers), plus an Orienter select whose choices
    are the guild's leadership and an "Any orienter (guild slot)" empty choice. A plain
    staff member gets the field locked to themselves (a crafted POST cannot override it).
    """

    date = forms.DateField(
        label="Date",
        widget=forms.DateInput(
            # Rule 14: the whole field opens the picker, and .pl-slot-date inverts the
            # black picker icon on the dark theme (reset under the light theme).
            attrs={"type": "date", "class": "pl-slot-date", "onclick": "try { this.showPicker() } catch (e) {}"}
        ),
    )
    start_time = forms.ChoiceField(choices=half_hour_time_choices(required=True), label="Start time")
    duration_minutes = forms.TypedChoiceField(
        coerce=int, choices=_SLOT_DURATION_CHOICES, initial="60", label="Duration"
    )
    orientation_type = forms.ModelChoiceField(
        queryset=OrientationType.objects.none(),
        label="Orientation",
        empty_label=None,
    )
    orienter = forms.ModelChoiceField(
        queryset=Member.objects.none(),
        required=False,
        label="Orienter",
        empty_label="Any orienter (guild slot)",
    )

    class Meta:
        model = OrientationSlot
        fields = ["seats", "location"]

    def __init__(
        self,
        *args: Any,
        guild: Guild,
        acting_member: Member | None = None,
        lock_to_acting: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._acting_member = acting_member
        self._lock_to_acting = lock_to_acting
        type_field = cast(forms.ModelChoiceField, self.fields["orientation_type"])
        type_field.queryset = OrientationType.objects.filter(guild=guild).active()
        type_field.error_messages["invalid_choice"] = "Pick one of this guild's orientations."
        first_type = guild.first_active_orientation_type()
        if first_type is not None:
            type_field.initial = first_type.pk
        leadership_ids = {member.pk for member in guild.leadership_members()}
        orienter_field = cast(forms.ModelChoiceField, self.fields["orienter"])
        orienter_field.queryset = Member.objects.filter(pk__in=leadership_ids).order_by("full_legal_name")
        orienter_field.error_messages["invalid_choice"] = "Pick someone on this guild's staff."
        if acting_member is not None and acting_member.pk in leadership_ids:
            orienter_field.initial = acting_member.pk
        if lock_to_acting:
            orienter_field.widget = forms.HiddenInput()

    def clean_orienter(self) -> Member | None:
        if self._lock_to_acting:
            # Plain staff add slots for themselves only — whatever the POST carried.
            return self._acting_member
        return cast("Member | None", self.cleaned_data.get("orienter"))

    def clean(self) -> dict[str, Any]:
        cleaned = cast(dict[str, Any], super().clean())
        day = cleaned.get("date")
        start_raw = cleaned.get("start_time")
        duration = cleaned.get("duration_minutes")
        if day and start_raw and duration:
            starts_at = timezone.make_aware(datetime.combine(day, _parse_time_choice(start_raw)))
            if starts_at <= timezone.now():
                self.add_error("date", "Pick a time in the future.")
            else:
                cleaned["starts_at"] = starts_at
                cleaned["ends_at"] = starts_at + timedelta(minutes=duration)
        return cleaned

    def save(self, commit: bool = True) -> OrientationSlot:
        slot = cast(OrientationSlot, super().save(commit=False))
        slot.starts_at = self.cleaned_data["starts_at"]
        slot.ends_at = self.cleaned_data["ends_at"]
        slot.orientation_type = self.cleaned_data["orientation_type"]
        slot.orienter = self.cleaned_data["orienter"]
        if commit:
            slot.save()
        return slot


class CommunityEventForm(forms.ModelForm):
    """Add/edit a FOG-native community event.

    One form serves three surfaces: a guild lead (``as_admin=False``) authors their
    guild's events (``event_type``/``guild`` are implied by context and removed from the
    form); an admin (``as_admin=True``) authors site-wide events and picks the
    type/guild; and a member (``as_member=True``) proposes an event with an *optional*
    guild picker (the type is derived — a guild picked → guild meeting, blank →
    community). The datetime widgets are copied from :class:`OrientationSlotForm`.
    """

    class Meta:
        model = CommunityEvent
        fields = [
            "event_type",
            "guild",
            "title",
            "starts_at",
            "ends_at",
            "location",
            "video_url",
            "description",
            "recurrence",
            "google_calendar_target",
            "publish_at",
            "remind_7d",
            "remind_3d",
            "remind_1d",
            "notify_happening_now",
        ]
        widgets = {
            "starts_at": forms.DateTimeInput(
                attrs={"type": "datetime-local", "onclick": "this.showPicker?.()"}, format="%Y-%m-%dT%H:%M"
            ),
            "ends_at": forms.DateTimeInput(
                attrs={"type": "datetime-local", "onclick": "this.showPicker?.()"}, format="%Y-%m-%dT%H:%M"
            ),
            "publish_at": forms.DateTimeInput(
                attrs={"type": "datetime-local", "onclick": "this.showPicker?.()"}, format="%Y-%m-%dT%H:%M"
            ),
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(
        self,
        *args: Any,
        guild: Guild | None = None,
        as_admin: bool = False,
        as_member: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        for name in ("starts_at", "ends_at", "publish_at"):
            cast(forms.DateTimeField, self.fields[name]).input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"]
        self.fields["publish_at"].label = "Announce at"
        self.fields["video_url"].label = "Video link"
        # The picker is a <select> that always submits a value in the UI; keep it forgiving so a
        # value-less POST falls back to the model default (MEMBER) rather than erroring.
        self.fields["google_calendar_target"].required = False
        self._as_admin = as_admin
        self._as_member = as_member
        self._fixed_guild = guild
        if as_member:
            # The proposer picks an optional guild; the type is derived on save.
            del self.fields["event_type"]
            self.fields["guild"].required = False
            self.fields["guild"].label = "Guild (optional)"
        elif as_admin:
            self.fields["guild"].required = False
        else:
            del self.fields["event_type"]
            del self.fields["guild"]
            # A lead authoring a NEW guild meeting defaults to the Public calendar — Google is now
            # a public mirror. The lead can still switch to the members-only calendar per event, and
            # editing an existing event keeps its saved target (only the create default changes).
            if not self.instance.pk:
                self.fields["google_calendar_target"].initial = CommunityEvent.GoogleCalendarTarget.PUBLIC

    def clean_google_calendar_target(self) -> str:
        """Coerce a blank/omitted picker value to the default MEMBER calendar."""
        return self.cleaned_data.get("google_calendar_target") or CommunityEvent.GoogleCalendarTarget.MEMBER

    def clean_publish_at(self) -> Any:
        """Blank ⇒ announce now (valid). A set time must be in the future and strictly
        before the event starts (announcing after it started is a mistake)."""
        publish_at = self.cleaned_data.get("publish_at")
        if publish_at is None:
            return publish_at
        if publish_at <= timezone.now():
            raise forms.ValidationError("Pick a time in the future.")
        starts = self.cleaned_data.get("starts_at")
        if starts is not None and publish_at >= starts:
            raise forms.ValidationError("The announcement time must be before the event starts.")
        return publish_at

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


_STUDIO_HOURS_WEEKDAYS: list[tuple[str, str]] = [
    ("0", "Monday"),
    ("1", "Tuesday"),
    ("2", "Wednesday"),
    ("3", "Thursday"),
    ("4", "Friday"),
    ("5", "Saturday"),
    ("6", "Sunday"),
]


def _next_weekday_anchor(weekday: int, start: time) -> datetime:
    """The next occurrence of ``weekday`` at ``start`` (aware, Portland local); rolls to the
    following week if this week's slot has already passed. The WEEKLY series then repeats off it."""
    now = timezone.localtime()
    days_ahead = (weekday - now.weekday()) % 7
    anchor = timezone.make_aware(datetime.combine(now.date() + timedelta(days=days_ahead), start))
    if anchor <= now:
        anchor = timezone.make_aware(datetime.combine(now.date() + timedelta(days=days_ahead + 7), start))
    return anchor


class StudioHoursForm(forms.ModelForm):
    """One weekly studio-hours block, edited as weekday + start/end time (+ optional location/note).

    A light *translating* ModelForm over :class:`CommunityEvent`: the friendly declared fields map
    to a WEEKLY, PUBLIC-targeted ``STUDIO_HOURS`` row on :meth:`save`, and on edit are derived back
    from the row's anchor. The guild FK is pinned via ``form_kwargs`` (this is a modelformset, not an
    inline formset). The times are half-hour ``<select>`` dropdowns (Rule 19) — the "HH:MM" choice
    is parsed back to a ``datetime.time`` on clean, and an existing off-grid value is preserved.
    """

    weekday = forms.ChoiceField(choices=_STUDIO_HOURS_WEEKDAYS, label="Day")
    start_time = forms.ChoiceField(choices=half_hour_time_choices(required=True), label="From")
    end_time = forms.ChoiceField(choices=half_hour_time_choices(required=True), label="To")
    location = forms.CharField(label="Location", required=False, max_length=200)
    note = forms.CharField(label="Note", required=False, max_length=500)

    class Meta:
        model = CommunityEvent
        fields: list[str] = []

    def __init__(self, *args: Any, guild: Guild, **kwargs: Any) -> None:
        self._guild = guild
        super().__init__(*args, **kwargs)
        instance = self.instance
        if instance is not None and instance.pk:
            local_start = timezone.localtime(instance.starts_at)
            local_end = timezone.localtime(instance.ends_at)
            self.fields["weekday"].initial = str(local_start.weekday())
            _seed_time_choice(self, "start_time", local_start.time())
            _seed_time_choice(self, "end_time", local_end.time())
            self.fields["location"].initial = instance.location
            self.fields["note"].initial = instance.description

    def clean_start_time(self) -> time:
        return _parse_time_choice(self.cleaned_data["start_time"])

    def clean_end_time(self) -> time:
        return _parse_time_choice(self.cleaned_data["end_time"])

    def clean(self) -> dict[str, Any]:
        cleaned = cast(dict[str, Any], super().clean())
        start = cleaned.get("start_time")
        end = cleaned.get("end_time")
        if start and end and end <= start:
            self.add_error("end_time", "End time must be after start time.")
        return cleaned

    def save(self, commit: bool = True) -> CommunityEvent:
        event = self.instance
        event.guild = self._guild
        event.event_type = CommunityEvent.EventType.STUDIO_HOURS
        event.recurrence = CommunityEvent.Recurrence.WEEKLY
        event.google_calendar_target = CommunityEvent.GoogleCalendarTarget.PUBLIC
        event.moderation_state = CommunityEvent.ModerationState.PUBLISHED
        event.title = f"{self._guild.name} Studio Hours"
        weekday = int(self.cleaned_data["weekday"])
        event.starts_at = _next_weekday_anchor(weekday, self.cleaned_data["start_time"])
        anchor_date = timezone.localtime(event.starts_at).date()
        event.ends_at = timezone.make_aware(datetime.combine(anchor_date, self.cleaned_data["end_time"]))
        event.location = self.cleaned_data.get("location") or ""
        event.description = self.cleaned_data.get("note") or ""
        if commit:
            event.save()
        return event


StudioHoursFormSet = forms.modelformset_factory(
    CommunityEvent, form=StudioHoursForm, fields=[], extra=0, can_delete=True
)


class EventDecisionForm(forms.Form):
    """A reviewer's decision on a proposed event (approve / request changes / decline).

    ``notes`` is required for the two outcomes that send the proposer a reason
    (changes / decline), so an empty note is a real validation error — never a silent
    redirect. Approvals need no note.
    """

    DECISION_CHOICES = [("approve", "Approve"), ("changes", "Request changes"), ("decline", "Decline")]

    decision = forms.ChoiceField(choices=DECISION_CHOICES)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def clean(self) -> dict[str, Any]:
        cleaned = cast(dict[str, Any], super().clean())
        decision = cleaned.get("decision")
        notes = (cleaned.get("notes") or "").strip()
        if decision in ("changes", "decline") and not notes:
            self.add_error("notes", "Add a note so the proposer knows why.")
        return cleaned


class OrientationCustomRequestForm(forms.Form):
    """A member proposing their own orientation time when no posted slot works.

    ``orientation_type`` picks which of the guild's orientations they want — its
    duration and price size the one-off slot and the checkout (issue #282).
    """

    orientation_type = forms.ModelChoiceField(
        queryset=OrientationType.objects.none(),
        label="Which orientation?",
        empty_label=None,
    )
    starts_at = forms.DateTimeField(
        label="Preferred time",
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"],
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local", "onclick": "this.showPicker?.()"}, format="%Y-%m-%dT%H:%M"
        ),
    )
    note = forms.CharField(label="Note (optional)", required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args: Any, guild: Guild | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if guild is not None:
            type_field = cast(forms.ModelChoiceField, self.fields["orientation_type"])
            type_field.queryset = OrientationType.objects.filter(guild=guild).active()
            type_field.error_messages["invalid_choice"] = "Pick one of this guild's orientations."
            first_type = guild.first_active_orientation_type()
            if first_type is not None:
                type_field.initial = first_type.pk

    def clean_starts_at(self) -> Any:
        starts = self.cleaned_data["starts_at"]
        if starts <= timezone.now():
            raise forms.ValidationError("Pick a time in the future.")
        return starts


class OrientationSlotChoiceField(forms.ModelChoiceField):
    """Slot dropdown whose labels surface seats held by checkouts in progress.

    Without this a lead sees a slot mysteriously full: holds consume seats but
    never appear in ``active()`` queries. Prefers the ``hold_count`` annotation
    (``with_pending_hold_count``); falls back to the per-row property.
    """

    def label_from_instance(self, obj: Any) -> str:
        holds = getattr(obj, "hold_count", None)
        if holds is None:
            holds = obj.pending_hold_count
        if not holds:
            return str(obj)
        noun = "seat" if holds == 1 else "seats"
        return f"{obj} — {holds} {noun} held by a checkout in progress"


class OrientationAddMemberForm(forms.Form):
    """Admin/lead adds a member to an orientation slot from the dashboard."""

    member = forms.ModelChoiceField(
        queryset=Member.objects.filter(status=Member.Status.ACTIVE).order_by("full_legal_name"),
        label="Member",
    )
    slot = OrientationSlotChoiceField(queryset=OrientationSlot.objects.none(), label="Slot")

    def __init__(self, *args: Any, slot_queryset: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if slot_queryset is not None:
            cast(forms.ModelChoiceField, self.fields["slot"]).queryset = slot_queryset


class OrientationBlockForm(forms.Form):
    """An orienter posts a one-off block of available time from the orientations dashboard.

    Type-agnostic on purpose (issue #283): a block belongs to a guild + orienter, and
    any of the guild's active orientation types may book into it. The orienter is
    always the acting member — you post your own time.
    """

    guild = forms.ModelChoiceField(queryset=Guild.objects.none(), label="Guild", empty_label=None)
    date = forms.DateField(
        label="Date",
        widget=forms.DateInput(
            # Rule 14: the whole field opens the picker; .pl-slot-date inverts the
            # black picker icon on the dark theme (reset under the light theme).
            attrs={"type": "date", "class": "pl-slot-date", "onclick": "try { this.showPicker() } catch (e) {}"}
        ),
    )
    start_time = forms.ChoiceField(choices=half_hour_time_choices(required=True), label="From")
    end_time = forms.ChoiceField(choices=half_hour_time_choices(required=True), label="Until")
    location = forms.CharField(max_length=200, required=False, label="Location (optional)")

    def __init__(self, *args: Any, guild_queryset: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if guild_queryset is not None:
            cast(forms.ModelChoiceField, self.fields["guild"]).queryset = guild_queryset

    def clean(self) -> dict[str, Any]:
        cleaned: dict[str, Any] = super().clean() or {}
        day = cleaned.get("date")
        start_choice = cleaned.get("start_time")
        end_choice = cleaned.get("end_time")
        if day and start_choice and end_choice:
            starts_at = timezone.make_aware(datetime.combine(day, _parse_time_choice(start_choice)))
            ends_at = timezone.make_aware(datetime.combine(day, _parse_time_choice(end_choice)))
            if ends_at <= starts_at:
                raise forms.ValidationError("The block has to end after it starts.")
            if starts_at <= timezone.now():
                raise forms.ValidationError("Pick a time in the future.")
            cleaned["starts_at"] = starts_at
            cleaned["ends_at"] = ends_at
        return cleaned


class OrientationBlockBookingForm(forms.Form):
    """A member books a start time inside an availability block (issue #283).

    The select's choices are the block's live valid starts for the picked type; the
    submitted value is re-validated as a datetime here and then rechecked under the
    block-row lock by the booking service, so a just-taken time fails with friendly copy.
    """

    orientation_type = forms.ModelChoiceField(queryset=OrientationType.objects.none(), widget=forms.HiddenInput())
    starts_at = forms.DateTimeField(
        label="Start time",
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"],
        widget=forms.Select(),
    )
    note = forms.CharField(label="Note (optional)", required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(
        self,
        *args: Any,
        block: OrientationAvailabilityBlock,
        orientation_type: OrientationType | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        type_field = cast(forms.ModelChoiceField, self.fields["orientation_type"])
        type_field.queryset = OrientationType.objects.filter(guild_id=block.guild_id).active()
        if orientation_type is not None:
            self.fields["orientation_type"].initial = orientation_type.pk
            start_field = self.fields["starts_at"]
            cast(forms.Select, start_field.widget).choices = [
                (
                    timezone.localtime(start).strftime("%Y-%m-%dT%H:%M"),
                    _meeting_time_label(timezone.localtime(start).hour, timezone.localtime(start).minute),
                )
                for start in block.valid_starts_for(orientation_type)
            ]


class GuildLeadForm(forms.Form):
    """Admin-only picker on the Staff tab — sets or replaces ``Guild.guild_lead``.

    Mirrors the ``set_guild_lead`` management command: any member may be chosen, and advisory
    conditions (not Active, no linked user) are surfaced as warnings by ``Guild.assign_lead``,
    never refusals.
    """

    member = forms.ModelChoiceField(queryset=Member.objects.none(), label="New guild lead")

    def __init__(self, *args: Any, member_queryset: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if member_queryset is not None:
            cast(forms.ModelChoiceField, self.fields["member"]).queryset = member_queryset


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

    def __init__(
        self, *args: Any, member_queryset: Any = None, guild: Any = None, allow_co_lead: bool = False, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        from membership.models import GuildStaffMembership

        self._guild = guild
        self._allow_co_lead = allow_co_lead
        # Only admins may mint Co-Leads (they carry lead-equivalent authority). Restricting the
        # field's choices both hides the option in the dropdown AND rejects a forged POST —
        # the gate lives here at the form level, not just in the template.
        role_choices = [
            (value, label)
            for value, label in GuildStaffMembership.Role.choices
            if allow_co_lead or value != GuildStaffMembership.Role.CO_LEAD
        ]
        cast(forms.ChoiceField, self.fields["role"]).choices = [("", "Choose a role…"), *role_choices]
        if member_queryset is not None:
            cast(forms.ModelChoiceField, self.fields["member"]).queryset = member_queryset

    def clean(self) -> dict[str, Any]:
        from membership.models import GuildStaffMembership

        cleaned: dict[str, Any] = super().clean() or {}
        if not self._allow_co_lead and (self.data.get("role") or "") == GuildStaffMembership.Role.CO_LEAD:
            # The submitted value already failed the restricted ChoiceField; replace the generic
            # "not one of the available choices" noise with the actual policy.
            raise forms.ValidationError("Only an admin can add a Co-Lead.")
        role = cleaned.get("role") or ""
        custom_title = (cleaned.get("custom_title") or "").strip()
        cleaned["custom_title"] = custom_title

        if role and custom_title:
            raise forms.ValidationError("Pick a preset role or type a custom title — not both.")
        if not role and not custom_title:
            raise forms.ValidationError("Pick a role or type a custom title.")

        preset_labels = {label.casefold() for _, label in GuildStaffMembership.Role.choices}
        if custom_title and custom_title.casefold() in preset_labels:
            raise forms.ValidationError("That title is already a preset role — pick it from the dropdown instead.")

        member = cleaned.get("member")
        if self._guild is not None and member is not None:
            held = self._guild.staff_memberships.filter(member=member)
            if role and held.filter(role=role).exists():
                label = GuildStaffMembership.Role(role).label
                raise forms.ValidationError(f"{member.display_name} is already {label} of this guild.")
            if custom_title and held.filter(custom_title__iexact=custom_title).exists():
                raise forms.ValidationError(f"{member.display_name} already holds the title “{custom_title}”.")
        return cleaned


class ChannelRadioSelect(forms.RadioSelect):
    """Radio group for the guild-announcement Discord channel picker.

    Renders each :class:`~membership.models.GuildAnnouncement.DiscordChannel` option as a
    radio. A channel whose webhook isn't configured *for this guild* is rendered as a real,
    greyed ``<input disabled>`` carrying a ``data-hint`` attribute so the picker partial can
    show a muted "Not set up yet." next to it. ``NONE`` ("Don't post to Discord") is always
    available. Keeping the disabled state on the widget (not a parallel template map) means
    the browser genuinely disables the input and the form/clean layer and the template read
    from one source of truth (:attr:`configured_channels`).
    """

    DISABLED_HINT = "Not set up yet."

    def __init__(self, *args: Any, configured_channels: set[str] | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.configured_channels: set[str] = set(configured_channels or set())

    def create_option(
        self,
        name: str,
        value: Any,
        label: Any,
        selected: bool,
        index: int,
        subindex: int | None = None,
        attrs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        channel = str(getattr(value, "value", value))
        if channel != GuildAnnouncement.DiscordChannel.NONE and channel not in self.configured_channels:
            option["attrs"]["disabled"] = True
            option["attrs"]["data-hint"] = self.DISABLED_HINT
        return option


def _configured_discord_channels(guild: Guild | None, config: SiteConfiguration | None = None) -> set[str]:
    """The set of :class:`GuildAnnouncement.DiscordChannel` values that have a webhook set.

    ``GUILD`` when this guild has its own ``discord_webhook_url``; ``GENERAL`` / ``LEADERSHIP`` /
    ``OFFICERS`` when the makerspace-wide :class:`~core.models.SiteConfiguration` webhooks are set.
    ``NONE`` is always selectable and is deliberately not listed here.

    Pass ``config`` to reuse an already-loaded :class:`~core.models.SiteConfiguration`
    singleton — building one picker per row (the review queue) would otherwise re-load it
    each call.
    """
    channels = GuildAnnouncement.DiscordChannel
    configured: set[str] = set()
    if guild is not None and (guild.discord_webhook_url or "").strip():
        configured.add(channels.GUILD.value)
    if config is None:
        config = SiteConfiguration.load()
    if (config.discord_general_webhook_url or "").strip():
        configured.add(channels.GENERAL.value)
    if (config.discord_leadership_webhook_url or "").strip():
        configured.add(channels.LEADERSHIP.value)
    if (config.discord_officers_webhook_url or "").strip():
        configured.add(channels.OFFICERS.value)
    return configured


def _default_discord_channel(configured: set[str]) -> str:
    """The pre-selected channel: the guild's OWN channel when configured, else "Don't post".

    A guild context never silently pre-selects a shared makerspace channel: a webhook
    less guild once stepped down to #general-chat and a test announcement reached the
    whole server (issue #271). Shared channels stay selectable, just never the default.
    """
    channels = GuildAnnouncement.DiscordChannel
    if channels.GUILD.value in configured:
        return channels.GUILD.value
    return channels.NONE.value


_CHANNEL_UNCONFIGURED_ERROR = "That Discord channel isn’t set up — pick another, or choose “Don’t post to Discord.”"


class GuildAnnouncementForm(forms.ModelForm):
    """Post a news announcement on a guild page.

    The "Also send email" toggle (default ON) chooses whether to also email the guild's
    joined members; the Discord **channel picker** chooses where the single Discord echo
    posts — this guild's own channel, the makerspace-wide #general-chat / #leadership, or
    "Don't post to Discord." ``GuildAnnouncement.notify_members`` reads the saved values;
    the in-app bell always fires.
    """

    class Meta:
        model = GuildAnnouncement
        fields = ["title", "body", "expires_at", "send_email", "discord_channel"]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 4}),
            "expires_at": forms.DateInput(attrs={"type": "date"}),
            "discord_channel": ChannelRadioSelect,
        }
        labels = {
            "expires_at": "Hide after (optional)",
            "send_email": "Also send email",
            "discord_channel": "Post to Discord channel",
        }
        help_texts = {"expires_at": "Leave blank to keep it up indefinitely."}

    def __init__(self, *args: Any, guild: Guild, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Not strictly required at the field level: this same ModelForm backs the edit
        # modal, which renders only title/body/expires_at — an omitted channel there must
        # leave the saved choice untouched (Django keeps the model default/existing value
        # when a defaulted field is omitted from the data), exactly like ``send_email``.
        self.fields["discord_channel"].required = False
        configured = _configured_discord_channels(guild)
        widget = cast(ChannelRadioSelect, self.fields["discord_channel"].widget)
        widget.configured_channels = configured
        if not self.is_bound and not (self.instance and self.instance.pk):
            self.fields["discord_channel"].initial = _default_discord_channel(configured)

    def clean_discord_channel(self) -> str:
        channel = cast(str, self.cleaned_data.get("discord_channel") or "")
        if not channel:
            return ""  # omitted (edit form) → the model default / existing value stands
        widget = cast(ChannelRadioSelect, self.fields["discord_channel"].widget)
        if channel != GuildAnnouncement.DiscordChannel.NONE and channel not in widget.configured_channels:
            raise forms.ValidationError(_CHANNEL_UNCONFIGURED_ERROR)
        return channel


class GuildAnnouncementProposalForm(forms.ModelForm):
    """A member proposing a guild announcement for a lead/admin to review.

    Any logged-in member can propose to any guild: they pick the guild and write the
    post, but NOT the outbound channels — a reviewer decides whether to also email the
    guild's members and post to Discord at approval time (see
    :class:`GuildAnnouncementDecisionForm`).
    """

    class Meta:
        model = GuildAnnouncement
        fields = ["guild", "title", "body", "expires_at"]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 4}),
            "expires_at": forms.DateInput(attrs={"type": "date"}),
        }
        labels = {"guild": "Guild", "expires_at": "Hide after (optional)"}
        help_texts = {
            "guild": "Which guild is this announcement for?",
            "expires_at": "Leave blank to keep it up indefinitely.",
        }

    def __init__(self, *args: Any, fixed_guild: Guild | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        guild_field = cast(forms.ModelChoiceField, self.fields["guild"])
        # New proposals may only target guilds that are taking member suggestions. An
        # existing proposal keeps its own guild selectable even if that guild has since
        # turned suggestions off, so a CHANGES_REQUESTED proposal can be revised and
        # resubmitted without being repointed at a different guild.
        active = Guild.objects.filter(is_active=True)
        if self.instance.pk:
            guild_field.queryset = active.filter(
                Q(allow_member_announcement_suggestions=True) | Q(pk=self.instance.guild_id)
            ).order_by("name")
        else:
            guild_field.queryset = active.filter(allow_member_announcement_suggestions=True).order_by("name")
        guild_field.required = True
        guild_field.error_messages["invalid_choice"] = "This guild isn't taking member suggestions right now."
        if fixed_guild is not None and not self.is_bound:
            guild_field.initial = fixed_guild.pk


class GuildAnnouncementSettingsForm(forms.ModelForm):
    """Guild-lead toggle for whether members may suggest announcements for this guild.

    A single boolean on :class:`~membership.models.Guild`, rendered as a toggle on the
    Announcements tab of the guild editor. Turning it off hides the member suggestion
    button and excludes the guild from the proposal form's guild picker for new proposals.
    """

    class Meta:
        model = Guild
        fields = ["allow_member_announcement_suggestions"]
        labels = {"allow_member_announcement_suggestions": "Let members suggest announcements"}


class GuildVisibilityForm(forms.ModelForm):
    """Admin-only show/hide toggle for a guild (Basic tab of the guild editor).

    A single boolean on :class:`~membership.models.Guild`, rendered as a toggle. Turning
    it off sets ``is_active=False``, which removes the guild from the sidebar, the guild
    directory, the community calendar, and voting — but the guild page and this settings
    page stay reachable by direct link, so an admin can turn it back on. Save is gated to
    an actual admin in the view; a guild lead never sees or reaches this control.
    """

    class Meta:
        model = Guild
        fields = ["is_active"]
        labels = {"is_active": "Visible to members"}
        help_texts = {
            "is_active": (
                "When off, this guild is hidden from the sidebar, the guild directory, the "
                "community calendar, and voting. Its guild page and this settings page stay "
                "reachable by direct link, so an admin can turn it back on."
            )
        }


class GuildAnnouncementDecisionForm(forms.Form):
    """A reviewer's decision on a proposed guild announcement.

    Approving also chooses the outbound channels: whether to email the guild's members
    (``send_email``, default on) and which Discord channel to post to (``discord_channel``,
    the same picker the lead's own post form uses) — the reviewer, not the proposer, owns
    those. Pass ``guild=`` so unconfigured channels render disabled and validate. The channel
    field is optional on the form because the changes/decline modals don't render it; the view
    only reads it on an approve. ``notes`` is required for the two outcomes that send the
    proposer a reason (changes / decline), so an empty note is a real validation error.
    """

    DECISION_CHOICES = [("approve", "Approve"), ("changes", "Request changes"), ("decline", "Decline")]

    decision = forms.ChoiceField(choices=DECISION_CHOICES)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    send_email = forms.BooleanField(required=False, initial=True)
    discord_channel = forms.ChoiceField(
        required=False,
        choices=GuildAnnouncement.DiscordChannel.choices,
        widget=ChannelRadioSelect,
        label="Post to Discord channel",
    )

    def __init__(
        self, *args: Any, guild: Guild | None = None, config: SiteConfiguration | None = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        configured = _configured_discord_channels(guild, config)
        widget = cast(ChannelRadioSelect, self.fields["discord_channel"].widget)
        widget.configured_channels = configured
        if not self.is_bound:
            self.fields["discord_channel"].initial = _default_discord_channel(configured)

    def clean_discord_channel(self) -> str:
        channel = cast(str, self.cleaned_data.get("discord_channel") or "")
        if not channel:
            return ""
        widget = cast(ChannelRadioSelect, self.fields["discord_channel"].widget)
        if channel != GuildAnnouncement.DiscordChannel.NONE and channel not in widget.configured_channels:
            raise forms.ValidationError(_CHANNEL_UNCONFIGURED_ERROR)
        return channel

    def clean(self) -> dict[str, Any]:
        cleaned = cast(dict[str, Any], super().clean())
        decision = cleaned.get("decision")
        notes = (cleaned.get("notes") or "").strip()
        if decision in ("changes", "decline") and not notes:
            self.add_error("notes", "Add a note so the proposer knows why.")
        return cleaned


class PushTestForm(forms.Form):
    """Admin push-test lookup — an email that must resolve to a real account.

    Backs ``/admin/push-test/``: on a valid submit the resolved ``User`` lands in
    ``cleaned_data["user"]`` for the view to inspect or fire a test push at.
    """

    email = forms.EmailField(
        label="Member email",
        help_text="The member to check. Their sign-in email or any linked alias works.",
    )

    def clean(self) -> dict[str, Any]:
        from core.push_admin import resolve_user

        cleaned = cast(dict[str, Any], super().clean())
        email = cleaned.get("email")
        if email:
            user = resolve_user(email)
            if user is None:
                self.add_error("email", "No account found with that email.")
            else:
                cleaned["user"] = user
        return cleaned


def split_audience(raw: str) -> "tuple[str, Guild | None, ClassOffering | None]":
    """Split the combined compose audience value into ``(audience, guild, class_offering)``.

    The wizard's single ``<select name="audience">`` carries one value per option —
    ``"site"`` for everyone, ``"guild:<pk>"`` for a guild's members, or ``"class:<pk>"``
    for a class's confirmed roster — so the UI stays one control while the model keeps its
    separate ``audience`` / ``guild`` / ``class_offering`` fields. A ``"guild:<pk>"`` or
    ``"class:<pk>"`` whose pk doesn't resolve returns the audience with a ``None`` target
    (the form / view then reject it). An unrecognized value returns ``("", None, None)``.
    """
    from classes.models import ClassOffering
    from membership.models import AnnouncementDraft

    if raw == AnnouncementDraft.Audience.SITE.value:
        return AnnouncementDraft.Audience.SITE.value, None, None
    if raw.startswith("guild:"):
        pk = raw.split(":", 1)[1]
        guild = Guild.objects.filter(pk=pk).first() if pk.isdigit() else None
        return AnnouncementDraft.Audience.GUILD.value, guild, None
    if raw.startswith("class:"):
        pk = raw.split(":", 1)[1]
        offering = ClassOffering.objects.filter(pk=pk).first() if pk.isdigit() else None
        return AnnouncementDraft.Audience.CLASS.value, None, offering
    return "", None, None


def discord_channel_choices(audience: str) -> list[tuple[str, str]]:
    """The Discord channel radio choices for an audience.

    A guild audience offers its own channel plus the shared ones; a site-wide audience
    drops "Our Guild Channel" (there is no single guild to post to). "Don't post" is
    always present.
    """
    channels = GuildAnnouncement.DiscordChannel
    from membership.models import AnnouncementDraft

    if audience == AnnouncementDraft.Audience.CLASS.value:
        # A class announcement is a direct notice to enrolled students — it never posts to
        # Discord, so the picker is absent (the send path ignores the channel for this audience).
        return []
    if audience == AnnouncementDraft.Audience.GUILD.value:
        return list(channels.choices)
    return [(channel.value, channel.label) for channel in channels if channel != channels.GUILD]


def _member_choice(user: Any) -> tuple[str, str]:
    """One ``("user:<pk>", "<name> · <email>")`` recipient checkbox row for ``user``."""
    return (f"user:{user.pk}", f"{(user.get_full_name() or user.get_username()).strip()} · {user.email}")


def announcement_recipient_choices(
    audience: str, guild: Guild | None, offering: Any = None, *, include_waitlist: bool = False
) -> list[tuple[str, str]]:
    """The recipient checklist choices for an audience — who gets the bell + push + email.

    A **guild** audience yields one ``("user:<pk>", "<name> · <email>")`` per member (from
    :meth:`Guild.announcement_recipients`, the exact fan-out) followed by one
    ``("custom:<addr>", "<addr>")`` per deduped custom mailing-list address (email-only). A
    **class** audience yields one row per confirmed registrant (plus waitlisted ones when
    ``include_waitlist`` is set): a registrant with a linked account is a ``user:<pk>`` row (bell +
    push + email); an email-only registrant (guest checkout, no account) is a ``custom:<addr>`` row
    (email only). A **site** audience yields ``[]`` — every member, too many to render as a
    checklist (the recipient card is absent there).
    """
    from membership.models import AnnouncementDraft

    if audience == AnnouncementDraft.Audience.GUILD.value and guild is not None:
        recipients = guild.announcement_recipients()
        member_emails = {(user.email or "").strip().lower() for user, _reason in recipients}
        choices = [_member_choice(user) for user, _reason in recipients]
        choices += [(f"custom:{addr}", addr) for addr in guild.mailing_list_emails_deduped(member_emails)]
        return choices
    if audience == AnnouncementDraft.Audience.CLASS.value and offering is not None:
        from classes.models import Registration

        class_choices: list[tuple[str, str]] = []
        seen_emails: set[str] = set()
        for registration in offering.announcement_recipients(include_waitlist=include_waitlist):
            suffix = " (waitlist)" if registration.status == Registration.Status.WAITLISTED else ""
            member = registration.member
            user = member.user if (member is not None and member.user is not None) else None
            if user is not None:
                value, label = _member_choice(user)
                class_choices.append((value, f"{label}{suffix}"))
                continue
            address = (registration.email or "").strip().lower()
            if not address or address in seen_emails:
                continue
            seen_emails.add(address)
            name = f"{registration.first_name} {registration.last_name}".strip()
            display = f"{name} · {registration.email}" if name else registration.email
            class_choices.append((f"custom:{address}", f"{display}{suffix}"))
        return class_choices
    return []


def announcement_add_member_choices() -> list[tuple[str, str]]:
    """Every active linked member as ``("user:<pk>", "<name> · <email>")`` — the "add anyone" list.

    Backs the composer's member-search datalist so a sender can add ANY member to the recipient
    set, even one outside the guild/class roster.
    """
    from django.contrib.auth.models import User

    members = User.objects.filter(is_active=True, member__isnull=False).order_by("first_name", "last_name", "username")
    return [_member_choice(user) for user in members]


class _RecipientChoiceField(forms.MultipleChoiceField):
    """A multi-select that silently DROPS values no longer in the roster (never errors).

    The roster can change between the wizard render and the submit (a member leaves, a custom
    row is deleted); an unknown value must be dropped, not raised (spec §5). The form's
    :meth:`AnnouncementComposeForm.clean` re-intersects the submission with the live choices.
    """

    def valid_value(self, value: str) -> bool:  # noqa: D102 - see class docstring
        return True


class AnnouncementComposeForm(forms.Form):
    """The compose wizard's single form — audience + message + email + Discord + @mention.

    One combined ``audience`` ``<select>`` value (``site`` / ``guild:<pk>``) is split in
    :meth:`clean` into the model's two fields via :func:`split_audience`, so the UI stays
    one control while the model + DB check constraint stay the source of truth. The
    audience the caller may address is enforced by the choices built in ``__init__`` (a
    non-admin never even sees "site") AND re-checked server-side in the send view. ``body``
    may be blank in a saved draft but is required to *send* — the send path passes
    ``require_body=True`` so ``clean_body`` rejects an empty message. Always sanitized.
    """

    audience = forms.ChoiceField(label="Who is this for?")
    body = forms.CharField(
        widget=RichTextEditorWidget(attrs={"rows": 10}),
        required=False,
        label="Message",
        help_text="Format with the toolbar — the formatted version goes out by email; "
        "the bell and Discord get a plain-text version.",
    )
    push_message = forms.CharField(
        required=False,
        max_length=180,
        label="Push Notification Message",
        widget=forms.Textarea(attrs={"rows": 2, "maxlength": "180"}),
        help_text="Max 180 characters.",
    )
    push_enabled = forms.BooleanField(required=False, initial=True, label="Send push notification")
    send_email = forms.BooleanField(required=False, initial=True, label="Send email")
    discord_enabled = forms.BooleanField(required=False, initial=True, label="Post to Discord")
    mark_as_urgent = forms.BooleanField(
        required=False,
        initial=False,
        label="Mark as urgent",
        help_text="Sends the email as transactional, overriding user email preferences.",
    )
    show_sender = forms.BooleanField(required=False, initial=True, label="Show who it's from")
    include_waitlist = forms.BooleanField(
        required=False,
        initial=False,
        label="Also include the waitlist",
        help_text="Class announcements only — send to waitlisted registrants too, not just confirmed ones.",
    )
    recipients = _RecipientChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Recipients",
    )
    discord_channel = forms.ChoiceField(required=False, widget=forms.Select, label="Discord channel")
    mention = forms.ChoiceField(
        required=False,
        widget=forms.Select,
        label="Ping",
    )
    expires_at = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Hide after (optional)",
        help_text="Guild announcements only. Leave blank to keep it up indefinitely.",
    )

    def __init__(
        self,
        *args: Any,
        is_admin: bool = False,
        editable_guilds: Any = None,
        editable_classes: Any = None,
        config: SiteConfiguration | None = None,
        require_body: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        from membership.models import AnnouncementDraft

        # ``require_body`` is set by the send path — a blank body is fine while drafting but
        # must be rejected before an announcement actually goes out.
        self._require_body = require_body
        self._config = config or SiteConfiguration.load()
        # Audience choices: "site" (admins only) + one per editable guild + one per class you teach.
        choices: list[tuple[str, str]] = []
        if is_admin:
            choices.append((AnnouncementDraft.Audience.SITE.value, "Everyone (site-wide)"))
        choices.extend((f"guild:{guild.pk}", guild.name) for guild in (editable_guilds or []))
        choices.extend((f"class:{offering.pk}", f"Class: {offering.title}") for offering in (editable_classes or []))
        cast(forms.ChoiceField, self.fields["audience"]).choices = choices

        # The raw audience currently in play (bound data > initial > first choice), split so
        # the Discord picker + target validation are scoped correctly on both GET and POST.
        self.audience_value = self._raw_audience(choices)
        self.current_audience, self.current_guild, self.current_class = split_audience(self.audience_value)

        # Recipient checklist (bell + push + email), scoped to the audience (empty for site).
        # Everyone is checked by default; a resumed draft's initial wins. ``add_member_choices``
        # is every member, for the "add anyone" search datalist. For a class, the "also include
        # the waitlist" toggle expands the roster to waitlisted registrants too.
        # Named ``waitlist_included`` (not ``include_waitlist``) so it does not shadow the
        # declared ``include_waitlist`` BooleanField — the field stays reachable as
        # ``form["include_waitlist"]`` for rendering; this is the resolved bool for scoping.
        self.waitlist_included = self._raw_include_waitlist()
        self.recipient_choices = announcement_recipient_choices(
            self.current_audience, self.current_guild, self.current_class, include_waitlist=self.waitlist_included
        )
        self.add_member_choices = announcement_add_member_choices()
        recipient_field = cast(_RecipientChoiceField, self.fields["recipients"])
        recipient_field.choices = self.recipient_choices
        if not self.is_bound and "recipients" not in self.initial:
            recipient_field.initial = [value for value, _label in self.recipient_choices]
        recipient_field.widget.attrs.setdefault("class", "pl-recipient-checklist__box")

        # Discord channel: a plain dropdown of the CONFIGURED channels (+ "Don't post"), with the
        # guild's own channel shown by its real #name when the sync command has fetched it.
        self._configured_channels = _configured_discord_channels(self.current_guild, self._config)
        channel_field = cast(forms.ChoiceField, self.fields["discord_channel"])
        channel_field.choices = self._discord_channel_dropdown_choices()

        # @mention picker. A guild whose Discord roles are configured can ping its own role(s)
        # — labeled "@<Guild>", the recommended default — alongside @here / @everyone / no ping.
        # Site announcements and role-less guilds default to NO ping: @everyone stays available
        # but must be an explicit choice (a webhook-less guild once defaulted to @everyone in
        # #general-chat and pinged the whole makerspace, issue #271). (Glass pings both of its
        # roles, since discord_role_ids holds every configured role for the guild.)
        mention_choices = [
            (AnnouncementDraft.Mention.EVERYONE.value, "@everyone"),
            (AnnouncementDraft.Mention.HERE.value, AnnouncementDraft.Mention.HERE.label),
        ]
        default_mention = AnnouncementDraft.Mention.NONE.value
        if self.current_guild is not None and self.current_guild.discord_role_ids:
            mention_choices.append((AnnouncementDraft.Mention.ROLE.value, f"@{self.current_guild.name}"))
            default_mention = AnnouncementDraft.Mention.ROLE.value
        mention_choices.append((AnnouncementDraft.Mention.NONE.value, AnnouncementDraft.Mention.NONE.label))
        cast(forms.ChoiceField, self.fields["mention"]).choices = mention_choices

        if not self.is_bound:
            channel_field.initial = self._default_dropdown_channel()
            self.fields["mention"].initial = default_mention

        # Alpine bindings for the two-phase composer (compose <-> preview). The URL-bearing hx-get
        # on the audience select is added at render time (the form must not reverse URLs). The
        # @click opens the native date picker from the whole field (FRONTEND Rule 14).
        self.fields["audience"].widget.attrs.setdefault("x-model", "audience")
        self.fields["push_enabled"].widget.attrs.setdefault("x-model", "pushOn")
        self.fields["send_email"].widget.attrs.setdefault("x-model", "emailOn")
        self.fields["discord_enabled"].widget.attrs.setdefault("x-model", "discordOn")
        self.fields["mark_as_urgent"].widget.attrs.setdefault("x-model", "urgentOn")
        self.fields["include_waitlist"].widget.attrs.setdefault("x-model", "includeWaitlist")
        self.fields["mention"].widget.attrs.setdefault("x-model", "mention")
        channel_field.widget.attrs.setdefault("x-model", "discordChannel")
        self.fields["expires_at"].widget.attrs.setdefault(
            "@click", "try { $event.currentTarget.showPicker() } catch (e) {}"
        )

    def _raw_audience(self, choices: list[tuple[str, str]]) -> str:
        """The combined audience value to scope by: bound data, else initial, else first choice."""
        if self.is_bound:
            return (self.data.get("audience") or "").strip()
        initial = self.initial.get("audience")
        if initial:
            return str(initial)
        return choices[0][0] if choices else ""

    def _raw_include_waitlist(self) -> bool:
        """Whether the waitlist is folded into the roster: bound data, else initial (default off).

        Read the same way as the audience so the recipient checklist is built correctly on the
        first GET, on the HTMX re-render when the toggle flips, and on the send POST.
        """
        if self.is_bound:
            return bool(self.data.get("include_waitlist"))
        return bool(self.initial.get("include_waitlist"))

    def clean_discord_channel(self) -> str:
        channel = cast(str, self.cleaned_data.get("discord_channel") or "")
        if not channel:
            return GuildAnnouncement.DiscordChannel.NONE.value
        if channel != GuildAnnouncement.DiscordChannel.NONE and channel not in self._configured_channels:
            raise forms.ValidationError(_CHANNEL_UNCONFIGURED_ERROR)
        return channel

    def _discord_channel_dropdown_choices(self) -> list[tuple[str, str]]:
        """The Discord channel dropdown options: configured channels + "Don't post".

        Only channels that are actually configured (a webhook set) appear, so the dropdown never
        offers a dead option. The guild's own channel is relabeled with its real ``#name`` when
        the ``sync_guild_discord_channels`` command has fetched it (else a generic fallback).
        """
        channels = GuildAnnouncement.DiscordChannel
        result: list[tuple[str, str]] = []
        for value, label in discord_channel_choices(self.current_audience):
            if value == channels.NONE.value:
                continue
            if value not in self._configured_channels:
                continue
            if value == channels.GUILD.value and self.current_guild is not None:
                label = self.current_guild.announcement_channel_label
            result.append((value, label))
        result.append((channels.NONE.value, channels.NONE.label))
        return result

    def _default_dropdown_channel(self) -> str:
        """Preselect the guild's OWN channel when configured, else "Don't post".

        A guild audience never silently preselects a shared site wide channel: a
        webhook less guild once defaulted to #general-chat and a test announcement
        reached the whole makerspace (issue #271). Site announcements keep the first
        configured shared channel, since posting site wide is their whole point.
        """
        channels = GuildAnnouncement.DiscordChannel
        if self.current_guild is not None:
            if channels.GUILD.value in self._configured_channels:
                return channels.GUILD.value
            return channels.NONE.value
        for value, _label in self._discord_channel_dropdown_choices():
            if value != channels.NONE.value:
                return value
        return channels.NONE.value

    def clean_body(self) -> str:
        # Blank allowed while drafting; required (and always sanitized) when sending.
        body = sanitize_rich_html(self.cleaned_data.get("body") or "")
        if self._require_body and not body:
            raise forms.ValidationError("Add a message before sending.")
        return body

    def clean(self) -> dict[str, Any]:
        from membership.models import AnnouncementDraft

        cleaned = cast(dict[str, Any], super().clean())
        audience, guild, offering = split_audience(cleaned.get("audience") or "")
        if not audience:
            raise forms.ValidationError("Choose who this announcement is for.")
        cleaned["audience"] = audience
        cleaned["guild"] = guild
        cleaned["class_offering"] = offering
        if audience == AnnouncementDraft.Audience.GUILD.value and guild is None:
            self.add_error("audience", "Choose a guild for this announcement.")
        if audience == AnnouncementDraft.Audience.CLASS.value and offering is None:
            self.add_error("audience", "Choose a class for this announcement.")
        cleaned["mention"] = cleaned.get("mention") or AnnouncementDraft.Mention.NONE.value
        cleaned["push_enabled"] = bool(cleaned.get("push_enabled"))
        cleaned["send_email"] = bool(cleaned.get("send_email"))
        cleaned["discord_enabled"] = bool(cleaned.get("discord_enabled"))
        cleaned["show_sender"] = bool(cleaned.get("show_sender"))
        cleaned["include_waitlist"] = bool(cleaned.get("include_waitlist"))
        cleaned["recipient_selection"] = self._clean_recipients(cleaned, audience)
        return cleaned

    @property
    def has_email_only_recipients(self) -> bool:
        """True when any roster row is an email-only address (no linked app account).

        Drives the composer's "email only, no push/bell" note: a class guest registrant or a guild
        custom mailing-list address can be reached by email but never by push or the in-app bell.
        """
        return any(value.startswith("custom:") for value, _label in self.recipient_choices)

    def _clean_recipients(self, cleaned: dict[str, Any], audience: str) -> dict[str, Any]:
        """Turn the submitted recipient checklist (+ any added members) into the stored selection.

        Members (``user:<pk>``) may be ANY member — a roster row OR one added via the "add anyone"
        search — so they validate against the full member list, not just the roster. Custom
        (``custom:<addr>``) values validate against the guild's mailing-list addresses. An
        unchanged submission (exactly the roster, nothing added or removed) collapses to ``{}`` =
        "everyone in the audience" (the default); anything else stores ``{"users": [...],
        "custom": [...]}``.
        """
        roster_values = {value for value, _label in self.recipient_choices}
        addable_users = {value for value, _label in self.add_member_choices}
        submitted = cleaned.get("recipients") or []
        chosen_users = [v for v in submitted if v.startswith("user:") and v in (roster_values | addable_users)]
        chosen_custom = [v for v in submitted if v.startswith("custom:") and v in roster_values]
        chosen = chosen_users + chosen_custom

        # Nothing chosen (an untouched or all-unchecked checklist — HTML omits unchecked boxes, so
        # the two are indistinguishable) OR exactly the roster → ``{}`` = everyone in the audience,
        # the default. Anything else (a subset, or an added off-roster member) is stored explicitly.
        if not chosen or set(chosen) == roster_values:
            return {}
        return {
            "users": [int(v.split(":", 1)[1]) for v in chosen_users],
            "custom": [v.split(":", 1)[1] for v in chosen_custom],
        }


class ReleaseAnnouncementForm(forms.Form):
    """Compose the sectioned release-update email (the Announcements tab's Release mode).

    Subject / preheader / intro are freeform; the feature **cards** are derived from the
    current release line's changelog — not created or deleted here — so each card gets a
    fixed pair of dynamically-named fields (``include_<i>`` toggle + ``screenshot_<i>``
    select), built in ``__init__`` from :func:`core.release_email.build_release_cards`.
    The screenshot select offers only slugs whose asset actually exists, so the admin
    can never pick an image that would render text-only. Validation ("include at least
    one feature") lives in :meth:`clean`.
    """

    subject = forms.CharField(max_length=300, label="Subject")
    preheader = forms.CharField(
        max_length=200,
        required=False,
        label="Inbox preview line",
        help_text="The gray preview line next to the subject in the inbox. ~90 characters.",
    )
    intro = forms.CharField(
        widget=RichTextEditorWidget(attrs={"rows": 4}),
        required=False,
        label="Intro",
        help_text="A short line above the features. Optional.",
    )

    def __init__(self, *args: Any, version: str | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        from core.release_email import build_release_cards, feature_shot_choices
        from plfog.version import VERSION

        self.version = version or VERSION
        self.cards = build_release_cards(self.version)
        # Captured *before* any per-card override mutates screenshot_url: True when the
        # changelog named a shot for this card but it hasn't been captured yet — drives
        # the composer's "hasn't been captured" note.
        self._card_default_uncaptured = [bool(c.slug) and not c.screenshot_url for c in self.cards]
        choices = feature_shot_choices()
        for index, card in enumerate(self.cards):
            self.fields[f"include_{index}"] = forms.BooleanField(
                required=False,
                initial=True,
                label="Include",
                widget=forms.CheckboxInput(
                    attrs={
                        "data-include-toggle": "1",
                        "@change": "includedCount += $event.target.checked ? 1 : -1",
                    }
                ),
            )
            # Default to the entry's own shot only when it's actually captured; otherwise
            # the card defaults to "No screenshot" rather than expecting a missing image.
            initial_slug = card.slug if card.screenshot_url else ""
            self.fields[f"screenshot_{index}"] = forms.ChoiceField(
                choices=choices, required=False, initial=initial_slug, label="Screenshot"
            )

    def card_rows(self) -> list[dict[str, Any]]:
        """``{card, include, screenshot, index, default_uncaptured}`` per feature, for the template list."""
        return [
            {
                "card": card,
                "include": self[f"include_{i}"],
                "screenshot": self[f"screenshot_{i}"],
                "index": i,
                "default_uncaptured": self._card_default_uncaptured[i],
            }
            for i, card in enumerate(self.cards)
        ]

    @property
    def included_count(self) -> int:
        """How many cards are currently toggled on (initial for an unbound form; else submitted)."""
        return sum(1 for i in range(len(self.cards)) if self[f"include_{i}"].value())

    def clean_intro(self) -> str:
        return sanitize_rich_html(self.cleaned_data.get("intro") or "")

    def clean(self) -> dict[str, Any]:
        cleaned = cast(dict[str, Any], super().clean())
        if not any(cleaned.get(f"include_{i}") for i in range(len(self.cards))):
            raise forms.ValidationError("Include at least one feature to send.")
        return cleaned

    def cleaned_cards(self) -> list[Any]:
        """The assembled cards with the admin's include/screenshot overrides applied.

        Mutates each :class:`~core.release_email.Card` in place: ``included`` from the
        toggle and ``screenshot_url`` re-resolved from the chosen slug (the title link
        stays tied to the changelog entry's own feature page).
        """
        from core.release_email import resolve_feature_shot_url

        for index, card in enumerate(self.cards):
            card.included = bool(self.cleaned_data[f"include_{index}"])
            card.screenshot_url = resolve_feature_shot_url(self.cleaned_data[f"screenshot_{index}"])
        return self.cards


class VotingSettingsForm(forms.ModelForm):
    """Admin form for the VotingSettings singleton (the Voting → Settings tab)."""

    class Meta:
        model = VotingSettings
        fields = [
            "reminder_lead_days",
            "minimum_pool_floor",
            "reminders_enabled",
            "send_vote_soon_enabled",
            "send_officer_reminder_enabled",
            "auto_snapshot_enabled",
        ]
        labels = {
            "reminder_lead_days": "Reminder lead time (days)",
            "minimum_pool_floor": "Minimum pool floor ($)",
            "reminders_enabled": "Send 'Polls closing soon!' reminders",
            "send_vote_soon_enabled": "Send 'Vote soon!' nudges",
            "send_officer_reminder_enabled": "Send officer turnout heads-up",
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


# ── Interactive space map ────────────────────────────────────────────────────


class FloorplanForm(forms.ModelForm):
    """One floor row on the map editor's Floors tab."""

    class Meta:
        model = Floorplan
        fields = ["name", "aspect_ratio", "image", "caption", "sort_order", "is_published"]
        widgets = {"sort_order": forms.HiddenInput()}
        labels = {
            "name": "Floor name",
            "aspect_ratio": "Shape (width ÷ height)",
            "image": "Reference underlay (optional)",
            "caption": "Caption",
            "is_published": "Show this floor on the map",
        }


FloorplanFormSet = forms.modelformset_factory(Floorplan, form=FloorplanForm, extra=0, can_delete=True)


class MapHotspotForm(forms.ModelForm):
    """One marker row on the map editor's Placement tab — **structural fields only**.

    Coordinates are deliberately absent: the visual editor owns ``x/y/w/h`` through
    :class:`MapHotspotPositionForm`, so saving this formset can never clobber a marker
    an admin just dragged. A row switched between region and pin has its dimensions
    reconciled here (a region with no box gets a centred default the admin can drag; a
    pin drops its box) because the model's ``ck_maphotspot_region_has_dims`` constraint
    would otherwise reject the save with a database error rather than a friendly one.
    """

    #: The centred starter box (percent w/h) a brand-new region gets before it's dragged.
    DEFAULT_REGION_W = Decimal("20.00")
    DEFAULT_REGION_H = Decimal("15.00")

    class Meta:
        model = MapHotspot
        fields = ["space", "kind", "shape", "label", "description", "guild", "sort_order"]
        widgets = {"sort_order": forms.HiddenInput(), "description": forms.Textarea(attrs={"rows": 2})}
        labels = {"space": "Linked space", "label": "Marker label", "guild": "Links to guild"}
        help_texts = {
            "guild": "Optional — a shop or room that is a guild's home links straight to its page.",
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        guild_field = cast(forms.ModelChoiceField, self.fields["guild"])
        guild_field.queryset = Guild.objects.order_by("name")

    def clean(self) -> dict[str, Any]:
        cleaned = cast(dict[str, Any], super().clean())
        kind = cleaned.get("kind")
        shape = cleaned.get("shape")
        space = cleaned.get("space")
        label = (cleaned.get("label") or "").strip()
        if kind in MapHotspot.REQUESTABLE_KINDS and space is None:
            self.add_error("space", "Pick the space this marker stands for.")
        # A wall is pure decoration — it carries neither a space nor a label.
        needs_label = kind not in MapHotspot.REQUESTABLE_KINDS and kind not in MapHotspot.DECORATIVE_KINDS
        if needs_label and space is None and not label:
            self.add_error("label", "Give this marker a label so members know what it is.")
        if shape == MapHotspot.Shape.REGION:
            if self.instance.w is None or self.instance.h is None:
                self.instance.w = self.DEFAULT_REGION_W
                self.instance.h = self.DEFAULT_REGION_H
        elif shape == MapHotspot.Shape.PIN:
            self.instance.w = None
            self.instance.h = None
        return cleaned


MapHotspotFormSet = forms.inlineformset_factory(Floorplan, MapHotspot, form=MapHotspotForm, extra=0, can_delete=True)


class MapHotspotEditForm(MapHotspotForm):
    """One marker's editor, opened by clicking its tile on the map.

    The map-first replacement for the old row-per-marker formset: an admin clicks a tile and
    edits everything members see about it — the same structural fields as :class:`MapHotspotForm`
    plus ``status``. ``status`` is not a marker field; it lives on the linked :class:`Space`
    (Airtable's system of record), so the *view* applies it after ``save()`` and the field is
    ignored for a marker with no space. Coordinates stay absent for the same two-write-path
    reason as the parent form — dragging owns ``x/y/w/h``.
    """

    status = forms.ChoiceField(
        required=False,
        choices=Space.Status.choices,
        label="Status",
        help_text="Only a marker linked to a space carries a status.",
    )

    class Meta(MapHotspotForm.Meta):
        fields = ["kind", "shape", "space", "label", "description", "guild"]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.space_id:
            self.fields["status"].initial = self.instance.space.status


class SpaceDetailEditForm(forms.ModelForm):
    """The in-modal, trimmed editor for one marker, opened from the public Spaces map popup.

    The routine-edit path (item 9): an admin edits exactly the four things members read on the
    space-detail card — ``status``, the ``guild`` link, the ``label`` and the ``description`` —
    straight from the popup, without opening the full "Edit This Map" placement editor. It is a
    strict subset of :class:`MapHotspotEditForm`: ``kind``/``shape``/``space`` stay structural
    (full editor only) and price/size stay read-only (Airtable owns them), so none of them are
    fields here. Like the placement editor, ``status`` is not a marker field — it lives on the
    linked :class:`Space` (Airtable's system of record), so the *view* applies it after ``save()``
    via ``_apply_marker_status`` and the field is ignored for a marker with no space.
    """

    status = forms.ChoiceField(
        required=False,
        choices=Space.Status.choices,
        label="Status",
        help_text="Only a marker linked to a space carries a status.",
    )

    class Meta:
        model = MapHotspot
        fields = ["label", "description", "guild"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}
        labels = {"label": "Marker label", "guild": "Links to guild"}
        help_texts = {
            "guild": "Optional — link this marker straight to a guild's page.",
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        guild_field = cast(forms.ModelChoiceField, self.fields["guild"])
        guild_field.queryset = Guild.objects.filter(is_active=True).order_by("name")
        if self.instance.pk and self.instance.space_id:
            self.fields["status"].initial = self.instance.space.status

    def clean(self) -> dict[str, Any]:
        cleaned = cast(dict[str, Any], super().clean())
        label = (cleaned.get("label") or "").strip()
        # A space-bound marker shows its space's code, so its label is free to be blank; a
        # facility/info marker shows its label, so that label can't be blanked away here.
        # Walls are decorative and never edited from the popup, but stay exempt for safety.
        needs_label = self.instance.space_id is None and self.instance.kind not in MapHotspot.DECORATIVE_KINDS
        if needs_label and not label:
            self.add_error("label", "Give this marker a label so members know what it is.")
        return cleaned


class MapHotspotPositionForm(forms.Form):
    """The visual editor's drag/resize payload for ONE marker, in image percentages.

    The only write path for coordinates. Bounds are enforced here (not in the view) so a
    dragged marker can never be stored running off the edge of its floor plan, and so the
    JSON endpoint's 400 carries a real, human message.
    """

    x = forms.DecimalField(max_digits=5, decimal_places=2, min_value=Decimal("0"), max_value=Decimal("100"))
    y = forms.DecimalField(max_digits=5, decimal_places=2, min_value=Decimal("0"), max_value=Decimal("100"))
    w = forms.DecimalField(
        max_digits=5, decimal_places=2, required=False, min_value=Decimal("0.01"), max_value=Decimal("100")
    )
    h = forms.DecimalField(
        max_digits=5, decimal_places=2, required=False, min_value=Decimal("0.01"), max_value=Decimal("100")
    )

    def __init__(self, *args: Any, hotspot: MapHotspot, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.hotspot = hotspot

    def clean(self) -> dict[str, Any]:
        cleaned = cast(dict[str, Any], super().clean())
        x, y = cleaned.get("x"), cleaned.get("y")
        w, h = cleaned.get("w"), cleaned.get("h")
        if self.hotspot.shape == MapHotspot.Shape.REGION:
            if w is None or h is None:
                raise forms.ValidationError("A region needs a width and height; a pin doesn't.")
            if x is not None and y is not None and (x + w > 100 or y + h > 100):
                raise forms.ValidationError("This marker runs off the edge of the floor plan.")
        else:
            cleaned["w"] = None
            cleaned["h"] = None
        return cleaned

    def error_message(self) -> str:
        """One flat sentence for the editor's error toast (the endpoint returns JSON)."""
        return " ".join(str(e) for errors in self.errors.values() for e in errors)

    def apply(self) -> MapHotspot:
        """Persist ONLY the coordinate fields — structural data is the formset's job."""
        self.hotspot.x = self.cleaned_data["x"]
        self.hotspot.y = self.cleaned_data["y"]
        self.hotspot.w = self.cleaned_data["w"]
        self.hotspot.h = self.cleaned_data["h"]
        self.hotspot.save(update_fields=["x", "y", "w", "h", "updated_at"])
        return self.hotspot


class SpaceRequestForm(forms.Form):
    """A member's one-field ask for a studio or cubby, raised from the map's detail panel."""

    message = forms.CharField(
        label="Anything we should know? (optional)",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "e.g. I'd use it for pottery storage."}),
    )

    def __init__(self, *args: Any, hotspot: MapHotspot, member: Member, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.hotspot = hotspot
        self.member = member
        self.fields["message"].help_text = f"This goes to {self._audience_label()}."

    def _audience_label(self) -> str:
        # Every request now goes to the makerspace admins (see SpaceRequest._notify_submitted).
        return "the makerspace admins"

    def clean(self) -> dict[str, Any]:
        cleaned = cast(dict[str, Any], super().clean())
        if not self.hotspot.is_requestable:
            raise forms.ValidationError("That space isn't open for requests right now.")
        if self.member.status != Member.Status.ACTIVE:
            raise forms.ValidationError("Requesting a space needs an active membership.")
        already = SpaceRequest.objects.filter(
            requester=self.member,
            space=self.hotspot.space,
            state=SpaceRequest.ModerationState.PENDING,
        ).exists()
        if already:
            raise forms.ValidationError("You already have a pending request for this space.")
        return cleaned

    def save(self) -> SpaceRequest:
        """Create the request and fire its reviewer notification."""
        kind = SpaceRequest.RequestKind.CUBBY if self.hotspot.cta_kind == "cubby" else SpaceRequest.RequestKind.LEASE
        request = SpaceRequest(
            space=self.hotspot.space,
            hotspot=self.hotspot,
            kind=kind,
            message=self.cleaned_data["message"],
        )
        request.submit(requester=self.member)
        return request


class SpaceRequestDecisionForm(forms.Form):
    """A reviewer's verdict on a space request.

    There is no changes-requested outcome — a space request has no editable body, so
    "not this one" is a decline, and a decline must carry a note the member can read.
    """

    DECISION_CHOICES = [("approve", "Approve"), ("decline", "Decline")]

    decision = forms.ChoiceField(choices=DECISION_CHOICES)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def clean(self) -> dict[str, Any]:
        cleaned = cast(dict[str, Any], super().clean())
        decision = cleaned.get("decision")
        notes = (cleaned.get("notes") or "").strip()
        if decision == "decline" and not notes:
            self.add_error("notes", "Add a note so the member knows why.")
        return cleaned


class TourStateForm(forms.Form):
    """Validation for the guided-tour state endpoint (Spec C §5).

    ``tour_key`` comes from the URL and is injected into the form's data by the
    view; an unknown key is a 404, a bad ``status`` a 400. A client can only
    ever write ``completed`` or ``dismissed`` — never ``offered``.
    """

    tour_key = forms.CharField()
    status = forms.ChoiceField(choices=[("completed", "completed"), ("dismissed", "dismissed")])

    def clean_tour_key(self) -> str:
        from core.tours import TOURS

        tour_key = self.cleaned_data["tour_key"]
        if tour_key not in TOURS:
            raise forms.ValidationError("Unknown tour.")
        return cast(str, tour_key)


class TourSettingsForm(forms.ModelForm):
    """The one-toggle "Guided tours" card in Settings → Notifications (Spec C §6.4)."""

    class Meta:
        model = Member
        fields = ["guided_tours_enabled"]


class EquipmentForm(forms.ModelForm):
    """Create/edit form for a piece of equipment (the Equipment directory, PR 1).

    Used by both the admin-gated add page and the manage panel's Details tab. The
    ``required_orientation`` choices narrow to the owning guild's active types when the
    equipment already belongs to a guild; otherwise every guild's active types are
    offered (grouped by guild via the type's ``__str__``) — the house Makerspace guild
    is an operating convention, not a code concept.
    """

    class Meta:
        model = Equipment
        fields = [
            "name",
            "kind",
            "guild",
            "space",
            "photo",
            "description",
            "location_note",
            "required_orientation",
            "requires_guild_membership",
            "is_active",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. CNC Router"}),
            "description": forms.Textarea(
                attrs={"rows": 5, "placeholder": "What is it, what can members make with it, any house rules."}
            ),
            "location_note": forms.TextInput(attrs={"placeholder": "e.g. Back corner of the wood shop"}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        guild_field = cast(forms.ModelChoiceField, self.fields["guild"])
        guild_field.queryset = Guild.objects.order_by("name")
        guild_field.empty_label = "Standalone"
        guild_field.required = False
        space_field = cast(forms.ModelChoiceField, self.fields["space"])
        space_field.queryset = Space.objects.order_by("space_id")
        space_field.empty_label = "No linked space"
        space_field.required = False
        orientation_field = cast(forms.ModelChoiceField, self.fields["required_orientation"])
        types = OrientationType.objects.active().select_related("guild").order_by("guild__name", "sort_order", "name")
        if self.instance.pk is not None and self.instance.guild_id is not None:
            types = types.filter(guild_id=self.instance.guild_id)
        orientation_field.queryset = types
        orientation_field.empty_label = "No orientation needed"
        orientation_field.required = False
        # Member-facing hints — the model help_text is written for admins/migrations and
        # would leak jargon (PROTECT, sync notes) into the form via form_field.html.
        self.fields["name"].help_text = ""
        self.fields["kind"].help_text = ""
        self.fields["guild"].help_text = "Blank means standalone equipment, run by the makerspace."
        self.fields["space"].help_text = "Optional. Link the physical room from the space map. We only read from it."
        self.fields["description"].help_text = ""
        self.fields["location_note"].help_text = "A short note that helps members find it."
        self.fields["required_orientation"].help_text = "Members must complete this orientation before they can book."
        self.fields["requires_guild_membership"].help_text = "Only members of the chosen guild can book."
        self.fields["is_active"].help_text = "Members can see and book this equipment. Turn off to retire it."
        self.fields["is_active"].label = "Active"

    def clean(self) -> dict[str, Any]:
        cleaned: dict[str, Any] = super().clean() or {}
        if cleaned.get("requires_guild_membership") and cleaned.get("guild") is None:
            raise forms.ValidationError("Pick a guild first, or turn this off.")
        return cleaned


class EquipmentStaffAddForm(forms.Form):
    """The manage panel's "+ Add Manager" form — grants one member a manager role."""

    member = forms.ModelChoiceField(queryset=Member.objects.none(), label="Member")

    def __init__(self, *args: Any, equipment: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._equipment = equipment
        cast(forms.ModelChoiceField, self.fields["member"]).queryset = Member.objects.active().order_by(
            "full_legal_name"
        )

    def clean_member(self) -> Member:
        member = cast(Member, self.cleaned_data["member"])
        if self._equipment is not None and self._equipment.staff_memberships.filter(member=member).exists():
            raise forms.ValidationError("They already manage this equipment.")
        return member
