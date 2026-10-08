"""The kiln ticket form and the crew's list forms (#691)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from django import forms
from django.core.exceptions import ValidationError

from core.validators import validate_image_content, validate_image_size
from kiln.models import ClayOption, GlazeOption, KilnTicket, ListOption

if TYPE_CHECKING:
    from django.core.files.uploadedfile import UploadedFile
    from django.db.models import QuerySet

OTHER = "other"
YES_NO = [("yes", "Yes"), ("no", "No")]

# The submit buttons. "draft" saves what is there; "submit" puts the ticket in the queue
# (or, on a ticket already in the queue, saves the edit). The photo buttons save the page
# first, so nothing typed is lost when a maker changes the cover or removes a photo.
ACTION_DRAFT = "draft"
ACTION_SUBMIT = "submit"
ACTION_COVER = "cover"
ACTION_REMOVE = "remove"


def _to_bool(value: str) -> bool | None:
    return {"yes": True, "no": False}.get(value)


class YesNoField(forms.TypedChoiceField):
    """A question answered Yes or No that starts with no answer."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            choices=YES_NO,
            coerce=_to_bool,
            empty_value=None,
            required=False,
            widget=forms.RadioSelect,
            **kwargs,
        )


class KilnTicketForm(forms.ModelForm):
    """Every question on a ticket. Nothing is required for a draft; Submit checks the rest.

    Args:
        action: Which button was pressed (``ACTION_*``), with the photo pk after a colon for
            the photo buttons. A ticket already in the queue checks every save as a submit.
        maker_photo_pks: Photos already on the ticket (for counting what Submit will see).
    """

    firing_type = forms.ChoiceField(
        choices=KilnTicket.FiringType.choices, required=False, widget=forms.RadioSelect, label="Type of firing"
    )
    clay_choice = forms.ChoiceField(required=False, label="Type of clay")
    walls_under_inch = YesNoField(label="Are all walls on this piece less than 1 inch thick?")
    bottom_free_of_glaze = YesNoField(label="Is the bottom 1/4 inch of this piece free of glaze?")
    stilts_added = YesNoField(
        label="Have you added appropriate stilts/cookies to your piece to protect the kiln shelves?"
    )
    studio_glazes = forms.ModelMultipleChoiceField(
        queryset=GlazeOption.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Which studio glazes?",
    )

    class Meta:
        model = KilnTicket
        fields = [
            "height_in",
            "width_in",
            "length_in",
            "quantity",
            "clay_other_name",
            "clay_other_cone6",
            "glaze_studio",
            "glaze_commercial",
            "commercial_glaze_name",
            "glaze_self_made",
            "self_made_glaze_description",
            "glaze_cone6",
        ]
        labels = {
            "height_in": "Height",
            "width_in": "Width",
            "length_in": "Length",
            "quantity": "Quantity of identical pieces",
            "clay_other_name": "Which clay?",
            "clay_other_cone6": "I confirm that this clay can be safely fired to a minimum of Cone 6",
            "glaze_studio": "Studio glaze",
            "glaze_commercial": "Commercial glaze",
            "commercial_glaze_name": "Which commercial glaze? Brand and name.",
            "glaze_self_made": "Self-made/other",
            "self_made_glaze_description": "What is the glaze?",
            "glaze_cone6": "I confirm that this clay and all glazes used can be safely fired to a minimum of Cone 6",
        }
        widgets = {
            "height_in": forms.NumberInput(attrs={"inputmode": "decimal", "step": "any", "min": "0"}),
            "width_in": forms.NumberInput(attrs={"inputmode": "decimal", "step": "any", "min": "0"}),
            "length_in": forms.NumberInput(attrs={"inputmode": "decimal", "step": "any", "min": "0"}),
            "quantity": forms.NumberInput(attrs={"inputmode": "numeric", "placeholder": "1", "min": "1"}),
        }

    def __init__(
        self, *args: Any, action: str = ACTION_DRAFT, maker_photo_pks: list[int] | None = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self.action, _, target = action.partition(":")
        self.target_photo_pk = int(target) if target.isdigit() else None
        self.maker_photo_pks = maker_photo_pks or []
        self.new_photos: list[UploadedFile] = []
        ticket: KilnTicket = self.instance
        cast("forms.ChoiceField", self.fields["clay_choice"]).choices = self._clay_choices(ticket)
        cast("forms.ModelMultipleChoiceField", self.fields["studio_glazes"]).queryset = self._glaze_queryset(ticket)
        if ticket.pk and not self.initial.get("clay_choice"):
            self.initial["clay_choice"] = (
                OTHER if ticket.clay_other else (str(ticket.clay_id) if ticket.clay_id else "")
            )
            for name in ("walls_under_inch", "bottom_free_of_glaze", "stilts_added"):
                value = getattr(ticket, name)
                self.initial[name] = "" if value is None else ("yes" if value else "no")
            self.initial["firing_type"] = ticket.firing_type
            self.initial["studio_glazes"] = [g.pk for g in ticket.studio_glazes.all()]

    @staticmethod
    def _clay_choices(ticket: KilnTicket) -> list[tuple[str, str]]:
        """The active clays, the ticket's own clay even if archived since, then Other."""
        clays = list(ClayOption.objects.active())
        if ticket.clay is not None and ticket.clay.is_archived:
            clays.append(ticket.clay)
        return [("", "Choose a clay")] + [(str(c.pk), c.name) for c in clays] + [(OTHER, "Other (specify)")]

    @staticmethod
    def _glaze_queryset(ticket: KilnTicket) -> QuerySet[GlazeOption]:
        """The active glazes, plus any the ticket already uses that were archived since."""
        query = GlazeOption.objects.active()
        if ticket.pk:
            query = GlazeOption.objects.filter(pk__in=query) | ticket.studio_glazes.all()
        return query.distinct().order_by("sort_order", "name")

    @property
    def is_strict(self) -> bool:
        """Submit, and every save of a ticket already in the queue, checks what Submit needs."""
        return self.action == ACTION_SUBMIT or self.instance.status != KilnTicket.Status.DRAFT

    @property
    def photo_count_after_save(self) -> int:
        removed = 1 if self.action == ACTION_REMOVE and self.target_photo_pk in self.maker_photo_pks else 0
        return len(self.maker_photo_pks) - removed + len(self.new_photos)

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        self._clean_photos()
        if self.action in (ACTION_COVER, ACTION_REMOVE) and self.target_photo_pk not in self.maker_photo_pks:
            raise ValidationError("That photo is not on this ticket.")
        choice = cleaned.get("clay_choice", "")
        self.instance.clay_other = choice == OTHER
        self.instance.clay = None if choice in ("", OTHER) else ClayOption.objects.get(pk=int(choice))
        self.instance.firing_type = cleaned.get("firing_type", "")
        for name in ("walls_under_inch", "bottom_free_of_glaze", "stilts_added"):
            setattr(self.instance, name, cleaned.get(name))
        if self.is_strict and not self.errors:
            self._check_submittable(cleaned)
        return cleaned

    def _clean_photos(self) -> None:
        """Validate every new upload: an image we can open, under the size cap."""
        for upload in self.files.getlist("photos") if self.files else []:
            try:
                validate_image_size(upload)
                validate_image_content(upload)
            except ValidationError as error:
                self.add_error(None, error)
                continue
            self.new_photos.append(upload)

    def _check_submittable(self, cleaned: dict[str, Any]) -> None:
        """Refuse Submit for what the ticket still needs, each under its own question."""
        probe = KilnTicket(
            firing_type=self.instance.firing_type,
            clay=self.instance.clay,
            clay_other=self.instance.clay_other,
            clay_other_name=cleaned.get("clay_other_name", ""),
            clay_other_cone6=cleaned.get("clay_other_cone6", False),
            glaze_studio=cleaned.get("glaze_studio", False),
            glaze_commercial=cleaned.get("glaze_commercial", False),
            commercial_glaze_name=cleaned.get("commercial_glaze_name", ""),
            glaze_self_made=cleaned.get("glaze_self_made", False),
            self_made_glaze_description=cleaned.get("self_made_glaze_description", ""),
            glaze_cone6=cleaned.get("glaze_cone6", False),
        )
        studio = cleaned.get("studio_glazes")
        missing = probe.missing_for_submit(
            photo_count=self.photo_count_after_save, studio_glaze_count=len(studio) if studio else 0
        )
        for answer in missing:
            self.add_error(None if answer.field == "photos" else answer.field, answer.message)

    def save(self, commit: bool = True) -> KilnTicket:
        ticket: KilnTicket = super().save(commit=False)
        ticket.drop_inapplicable_answers()
        if commit:
            ticket.save()
            self.save_glazes(ticket)
        return ticket

    def save_glazes(self, ticket: KilnTicket) -> None:
        """Store the studio glaze ticks, or clear them when the glaze branch no longer asks."""
        if ticket.keeps_studio_glazes:
            ticket.studio_glazes.set(self.cleaned_data.get("studio_glazes") or [])
        else:
            ticket.studio_glazes.clear()


class ListOptionForm(forms.Form):
    """Add or rename a clay or studio glaze."""

    name = forms.CharField(max_length=80, label="Name", error_messages={"required": "Give it a name."})

    def __init__(self, *args: Any, model: type[ListOption], instance: ListOption | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.model = model
        self.instance = instance

    def clean_name(self) -> str:
        name: str = self.cleaned_data["name"]
        clash = self.model.objects.filter(name__iexact=name, archived_at__isnull=True)  # type: ignore[attr-defined]
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise ValidationError(f"{name} is already on the list.")
        return name


class ToastForm(forms.Form):
    """A form whose refusal is one toast: its first error."""

    @property
    def first_error(self) -> str:
        return str(next(iter(self.errors.values()))[0])


class LoadKilnForm(ToastForm):
    """Confirm loaded: the firing's type and the tickets ticked on Load the Kiln.

    A ticked ticket is any ticket that exists; whether it can still go in (in the queue, of
    this firing's type) is decided under a row lock when the load runs
    (:func:`kiln.services.load_kiln`), never here, where it could change a moment later.
    """

    firing_type = forms.ChoiceField(
        choices=KilnTicket.FiringType.choices,
        error_messages={"required": "Choose bisque or glaze.", "invalid_choice": "Choose bisque or glaze."},
    )
    tickets = forms.ModelMultipleChoiceField(
        queryset=KilnTicket.objects.all(),
        error_messages={
            "required": "Tick at least one ticket to load.",
            "invalid_choice": "One of the ticked tickets no longer exists.",
            "invalid_pk_value": "One of the ticked tickets no longer exists.",
        },
    )


class ReplyForm(ToastForm):
    """A message on a ticket's thread."""

    body = forms.CharField(
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Write a message"}),
        label="Message",
        error_messages={"required": "Write a message first.", "max_length": "Keep it under 2000 characters."},
    )


class ManualFlagForm(ToastForm):
    """A flag the crew adds by hand. The note is for the crew only."""

    note = forms.CharField(
        max_length=300,
        widget=forms.Textarea(
            attrs={"rows": 2, "placeholder": "Example: glaze is thick near the foot, check before loading"}
        ),
        label="What should the crew check?",
        error_messages={"required": "Say what the crew should check.", "max_length": "Keep it under 300 characters."},
    )
