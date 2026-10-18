"""The HQ forms: the hub with its goals, one card with its shifts, and a volunteer's Your Info step."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.db.models import Prefetch
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils import timezone

from core.validators import validate_image_content, validate_image_size
from hq.models import EventHub, HubCard, HubGoal, VolunteerShift
from hq.services import VolunteerInfo
from membership.models import CommunityEvent, Member

# Path segments under /hq/ that are routes, not hubs.
RESERVED_SLUGS: frozenset[str] = frozenset({"new"})


class EventChoiceField(forms.ModelChoiceField):
    """The event picker names each published event with its date, so two with one title tell apart."""

    def label_from_instance(self, obj: Any) -> str:
        event: CommunityEvent = obj
        return f"{event.title} · {timezone.localtime(event.starts_at).strftime('%a, %b %-d, %Y')}"


class HubForm(forms.ModelForm):
    event = EventChoiceField(
        queryset=CommunityEvent.objects.published().order_by("starts_at"),
        required=False,
        label="Event",
        empty_label="No event yet",
        help_text="The published calendar event this HQ organises. Its date and location fill the hero band.",
    )

    class Meta:
        model = EventHub
        fields = ["title", "slug", "eyebrow", "event", "hero_image", "intro", "footer_note", "is_open"]
        labels = {
            "slug": "Address",
            "eyebrow": "Eyebrow line",
            "hero_image": "Hero image",
            "intro": "Intro",
            "footer_note": "Footer note",
            "is_open": "Open",
        }
        help_texts = {
            "is_open": "Members reach an open HQ from the sidebar and /hq/. Only one HQ can be open at a time.",
        }
        widgets = {
            "intro": forms.Textarea(attrs={"rows": 4}),
            "footer_note": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_slug(self) -> str:
        slug: str = self.cleaned_data["slug"]
        if slug in RESERVED_SLUGS:
            raise ValidationError("That address is taken by the HQ pages themselves. Choose another.")
        return slug

    def clean_hero_image(self) -> Any:
        """A fresh upload must be an image we can open, under the size cap; a stored file passes."""
        image = self.cleaned_data["hero_image"]
        if isinstance(image, UploadedFile):
            validate_image_size(image)
            validate_image_content(image)
        return image

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        if cleaned.get("is_open"):
            other = EventHub.objects.filter(is_open=True).exclude(pk=self.instance.pk).first()
            if other is not None:
                self.add_error("is_open", f"{other.title} is open. Close it first; only one HQ can be open.")
        return cleaned


class HubGoalForm(forms.ModelForm):
    class Meta:
        model = HubGoal
        fields = ["label", "kind", "target", "current_value", "is_money"]
        labels = {"kind": "Counted from", "current_value": "Where it stands", "is_money": "Dollars"}


class HubGoalBaseFormSet(BaseInlineFormSet):
    """The goals in page order: each saved row takes its position in the formset as ``sort_order``."""

    def save(self, commit: bool = True) -> list[HubGoal]:
        saved: list[HubGoal] = super().save(commit=False)
        for goal in self.deleted_objects:
            goal.delete()
        position = 0
        for form in self.forms:
            if form in self.deleted_forms:
                continue
            if form.instance.pk is None and not form.has_changed():
                # A row added with + Add and left blank: the formset skipped it, and so does this.
                continue
            form.instance.sort_order = position
            form.instance.save()
            position += 1
        return saved


HubGoalFormSet = inlineformset_factory(
    EventHub,
    HubGoal,
    form=HubGoalForm,
    formset=HubGoalBaseFormSet,
    extra=0,
    can_delete=True,
)


@dataclass(frozen=True)
class MemberRow:
    """One line of the named members checklist."""

    pk: int
    name: str
    email: str
    checked: bool

    @property
    def search_text(self) -> str:
        """What the search box matches, lowercased."""
        return f"{self.name} {self.email}".lower()


class HubCardForm(forms.ModelForm):
    named_members = forms.ModelMultipleChoiceField(
        queryset=Member.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Named members",
        help_text="People who may open this card whatever its audience, and who organise what is behind it.",
    )

    class Meta:
        model = HubCard
        fields = ["title", "who_for", "bullets", "button_text", "audience", "is_hidden", "named_members"]
        labels = {"who_for": "Who it is for", "button_text": "Button text", "is_hidden": "Hidden"}
        help_texts = {
            "bullets": "One per line, up to 4.",
            "is_hidden": "A hidden card shows to admins only, with a Hidden chip.",
        }
        widgets = {
            "bullets": forms.Textarea(attrs={"rows": 5}),
            "audience": forms.RadioSelect,
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["named_members"].queryset = _active_members()  # type: ignore[attr-defined]  # a ModelMultipleChoiceField

    def clean_bullets(self) -> str:
        lines = [line.strip() for line in self.cleaned_data["bullets"].splitlines() if line.strip()]
        if len(lines) > HubCard.MAX_BULLETS:
            raise ValidationError(f"Up to {HubCard.MAX_BULLETS} bullets, one per line. This has {len(lines)}.")
        return "\n".join(lines)

    def member_rows(self) -> list[MemberRow]:
        """Every ACTIVE member for the checklist, ticked as the form stands (posted or saved), in two queries."""
        chosen = {str(value) for value in self["named_members"].value() or []}
        members = _active_members().select_related("user").prefetch_related(_primary_emails())
        return [
            MemberRow(
                pk=member.pk, name=member.display_name, email=member.primary_email, checked=str(member.pk) in chosen
            )
            for member in members
        ]


def _active_members() -> Any:
    return Member.objects.active().order_by("full_legal_name", "pk")


def _primary_emails() -> Prefetch:
    """Prefetch each member's primary address, where ``Member.primary_email`` reads it."""
    from allauth.account.models import EmailAddress

    return Prefetch(
        "user__emailaddress_set",
        queryset=EmailAddress.objects.filter(primary=True),
        to_attr="_primary_emailaddresses",
    )


class VolunteerInfoForm(forms.Form):
    """The Your Info step on the volunteer page: who is signing up.

    ``website`` is a honeypot: it is hidden from people and labelled to be left blank, so a
    bot that fills every field gives itself away and the view reports success while saving
    nothing.
    """

    name = forms.CharField(max_length=120, label="Name")
    email = forms.EmailField(label="Email", help_text="How the organisers reach you. One sign-up per email on a shift.")
    phone = forms.CharField(max_length=40, required=False, label="Phone", help_text="Optional, for the night of.")
    has_olcc = forms.BooleanField(
        required=False,
        label="I have an Oregon OLCC service permit (needed to bartend)",
    )
    note = forms.CharField(
        required=False,
        label="Anything we should know?",
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Hours you can do, a skill, a friend you are coming with.",
    )
    website = forms.CharField(
        required=False,
        label="Leave this blank",
        widget=forms.TextInput(attrs={"autocomplete": "off", "tabindex": "-1"}),
    )

    @property
    def is_honeypot_hit(self) -> bool:
        """Whether the hidden field was filled, which only a bot does."""
        return bool(self.cleaned_data["website"])

    def to_info(self) -> VolunteerInfo:
        data = self.cleaned_data
        return VolunteerInfo(
            name=data["name"].strip(),
            email=data["email"].strip().lower(),
            phone=data["phone"].strip(),
            has_olcc=data["has_olcc"],
            note=data["note"].strip(),
        )


class VolunteerShiftForm(forms.ModelForm):
    class Meta:
        model = VolunteerShift
        fields = ["phase", "role", "when_text", "need", "description", "requires_olcc", "sort_order"]
        labels = {
            "when_text": "When",
            "need": "People needed",
            "requires_olcc": "Needs an OLCC permit",
            "sort_order": "Sort order",
        }
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}


class VolunteerShiftBaseFormSet(BaseInlineFormSet):
    """The shifts on the volunteers card's edit page.

    Deleting a shift people have signed up for is refused by name, so a sign-up is never
    dropped by a tidy up; the row's Delete button is not even offered while the shift has
    sign-ups, and this guards a page that went stale.
    """

    def clean(self) -> None:
        super().clean()
        refused = []
        for form in self.deleted_forms:
            count = form.instance.signups.count()
            if count:
                people = "1 person" if count == 1 else f"{count} people"
                # The role as saved: the posted row may have renamed it (or blanked it) on the same Save.
                refused.append(f"{form.initial['role']} has {people} signed up")
        if refused:
            raise ValidationError(f"A shift with sign-ups cannot be deleted: {'; '.join(refused)}.")


VolunteerShiftFormSet = inlineformset_factory(
    EventHub,
    VolunteerShift,
    form=VolunteerShiftForm,
    formset=VolunteerShiftBaseFormSet,
    extra=0,
    can_delete=True,
)
