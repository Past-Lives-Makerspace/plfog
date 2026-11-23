"""The HQ forms: the hub with its goals, one card with its shifts, a volunteer's Your Info step, Vendor Tables' add form,
row edit and space map capacity, and the PL Ledger's: the approval, budget lines, entries and receipts (D6a), the bar
sales and drink prices (D6b), and the door clicker, launch checklist, staff time and guild demos (D6c)."""

from __future__ import annotations

import re

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from pathlib import Path

from django import forms
from django.conf import settings
from django.core.validators import URLValidator
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils import timezone

from core.validators import validate_image_content, validate_image_pixels, validate_image_size
from hq.models import (
    BAR_AMOUNT_MAX_CENTS,
    BAR_QTY_MAX,
    PRICED_DRINKS,
    RECEIPT_KINDS,
    BarDrink,
    BarPay,
    BudgetLine,
    DOOR_STEPS,
    EventHub,
    GuildDemo,
    HubCard,
    HOURS_MAX,
    HOURS_MIN,
    HOURS_STEP,
    HubGoal,
    LaunchTask,
    LedgerEntry,
    LedgerKind,
    LedgerSettings,
    ShiftSignup,
    StaffTime,
    VendorTable,
    VolunteerShift,
)
from hq.services import (
    BAR_MIXED_NEEDS_TOTAL,
    BAR_NOTHING_ENTERED,
    DRINK_PRICE_FIELDS,
    VENDOR_CAPACITY_MAX,
    VolunteerInfo,
    drink_price_cents,
)
from hq.vendor_import import MAX_IMPORT_BYTES, TOO_LARGE, ImportRefused, read_csv
from membership.models import CommunityEvent, Guild

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


class HubCardForm(forms.ModelForm):
    """The card edit page: the card's words and Hidden."""

    class Meta:
        model = HubCard
        fields = ["title", "who_for", "bullets", "button_text", "is_hidden"]
        labels = {
            "who_for": "Who it is for",
            "button_text": "Button text",
            "is_hidden": "Hidden",
        }
        help_texts = {
            "bullets": "One per line, up to 4.",
            "is_hidden": "Marks a card still being set up: it shows with a Hidden chip.",
        }
        widgets: dict[str, Any] = {"bullets": forms.Textarea(attrs={"rows": 5})}

    def clean_bullets(self) -> str:
        lines = [line.strip() for line in self.cleaned_data["bullets"].splitlines() if line.strip()]
        if len(lines) > HubCard.MAX_BULLETS:
            raise ValidationError(f"Up to {HubCard.MAX_BULLETS} bullets, one per line. This has {len(lines)}.")
        return "\n".join(lines)


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
        max_length=ShiftSignup.MAX_NOTE_LENGTH,
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
            if form.instance.pk is None:
                # A Delete for a shift this hub no longer has (a stale page) or never had (a
                # foreign pk): there is no row to guard, and save() skips it rather than deleting.
                continue
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


# ---- Shared field helpers ----

# The whole field opens the date picker, not just its icon (FRONTEND rule 14).
_DATE_PICKER = {"type": "date", "@click": "(() => { try { $el.showPicker() } catch (e) {} })()"}


# ---- Vendor Tables (Track D, part 3) ----

# The empty choice of the guild select: a request with no guild.
NO_GUILD_LABEL = "Not in a guild"


def _offer_guilds(form: forms.ModelForm, guilds: Sequence[Guild]) -> dict[int, Guild]:
    """Give ``form``'s ``guild`` select the active ``guilds`` (read once by the view) and Not in a guild.

    A guild made inactive after the row named it stays offered on that row, so a Save does not drop it. Returns
    the offered guilds by pk, for ``clean_guild``.
    """
    offered = list(guilds)
    current = form.instance.guild if form.instance.guild_id is not None else None
    if current is not None and all(guild.pk != current.pk for guild in offered):
        offered.append(current)
    form.fields["guild"].choices = [  # type: ignore[attr-defined]  # a TypedChoiceField
        ("", NO_GUILD_LABEL),
        *((guild.pk, guild.name) for guild in offered),
    ]
    return {guild.pk: guild for guild in offered}


class VendorTableForm(forms.ModelForm):
    """+ Add a Table Request: Lee's eight fields. The guild is one of the active guilds (``guilds``), or none.

    The guild select takes its choices from the list the view read once, so a page of rows asks the
    database for the guilds a single time. Every new request starts as requested, or on the waitlist
    while the space map is full (``hq.services.add_vendor_table`` decides).
    """

    guild = forms.TypedChoiceField(required=False, coerce=int, empty_value=None, label="Guild")

    class Meta:
        model = VendorTable
        fields = ["name", "guild", "sells", "size", "email", "phone", "setup", "needs"]
        labels = {
            "name": "Artist or group",
            "sells": "What they sell",
            "size": "Table size",
            "email": "Email",
            "phone": "Phone",
            "setup": "Setup day",
            "needs": "Needs",
        }
        widgets: dict[str, forms.Widget] = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. Riverbend Ceramics"}),
            "sells": forms.TextInput(attrs={"placeholder": "e.g. Mugs and planters"}),
            "email": forms.EmailInput(attrs={"autocomplete": "off"}),
            "phone": forms.TextInput(attrs={"type": "tel", "autocomplete": "off"}),
            "needs": forms.TextInput(attrs={"placeholder": "e.g. Power, near a wall"}),
        }

    def __init__(self, *args: Any, guilds: Sequence[Guild], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._guilds = _offer_guilds(self, guilds)
        for name in self._meta.fields or ():
            self.fields[name].help_text = ""

    def clean_guild(self) -> Guild | None:
        pk: int | None = self.cleaned_data["guild"]
        return None if pk is None else self._guilds[pk]

    def to_fields(self) -> dict[str, Any]:
        """The request's fields for ``hq.services``, as cleaned (the text fields arrive trimmed)."""
        return {name: self.cleaned_data[name] for name in self._meta.fields or ()}


class VendorTableEditForm(VendorTableForm):
    """One row of the list: every field of the add form plus the status and the table number, saved as each changes.

    Rendered across the row's cells, each control tied to the row's form by its ``form`` attribute
    (``form_id``), since a form cannot wrap a table row. Each label names the row ("Email for Riverbend
    Ceramics"), so a screen reader moving through the cells hears whose field it is in.

    ``updated_at`` carries the row's stamp as the page saw it; :attr:`is_stale` is a Save from a page older
    than the row, which the view refuses whole.
    """

    updated_at = forms.CharField(required=False, widget=forms.HiddenInput)

    class Meta(VendorTableForm.Meta):
        fields = [
            *VendorTableForm.Meta.fields,
            "status",
            "table_no",
            "note",
            "requested_on",
            "request_source",
            "discord_handle",
        ]
        labels = {
            **VendorTableForm.Meta.labels,
            "status": "Status",
            "table_no": "Table #",
            "note": "Source",
            "requested_on": "Requested on",
            "request_source": "Request source",
            "discord_handle": "Discord handle",
        }
        widgets: dict[str, forms.Widget] = {
            **VendorTableForm.Meta.widgets,
            "name": forms.TextInput(),
            "sells": forms.TextInput(attrs={"placeholder": "What"}),
            "needs": forms.TextInput(attrs={"placeholder": "Needs"}),
            "email": forms.EmailInput(attrs={"autocomplete": "off", "placeholder": "Email"}),
            "phone": forms.TextInput(attrs={"type": "tel", "autocomplete": "off", "placeholder": "Phone"}),
            "table_no": forms.TextInput(attrs={"placeholder": "#"}),
            "note": forms.TextInput(attrs={"placeholder": "Source"}),
            "requested_on": forms.DateInput(format="%Y-%m-%d", attrs=_DATE_PICKER),
            "request_source": forms.TextInput(attrs={"placeholder": "How it came in"}),
            "discord_handle": forms.TextInput(attrs={"autocomplete": "off", "placeholder": "Discord"}),
        }

    def __init__(self, *args: Any, form_id: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.form_id = form_id
        self.fields["updated_at"].initial = self._stamp
        # The stamp sits in the row's state slot, outside the <form>, so it is tied to the form like every control.
        self.fields["updated_at"].widget.attrs["form"] = form_id
        for name in self._meta.fields or ():
            field = self.fields[name]
            field.widget.attrs["form"] = form_id
            field.label = f"{field.label} for {self.instance.name}"

    @property
    def _stamp(self) -> str:
        """The row's ``updated_at`` as the hidden field carries it; an edit form's row is always saved."""
        return self.instance.updated_at.isoformat()

    @property
    def is_stale(self) -> bool:
        """Whether the posted stamp differs from the row's: someone saved it after this page loaded."""
        return self.is_bound and self.data.get(self.add_prefix("updated_at"), "") != self._stamp


class VendorImportForm(forms.Form):
    """Import CSV: one file in the columns of Kate's sheet (or the page's own export), read whole or refused whole.

    ``clean_file`` reads it into rows (``hq.vendor_import.read_csv``), so a file over 1 MB, not UTF-8, not a CSV or
    over 1000 rows is a form error with nothing written; the view parses the rows against the active guilds.
    """

    file = forms.FileField(
        label="CSV file",
        error_messages={
            "required": "Choose a CSV file to import.",
            "empty": "That file is empty. Nothing was imported.",
        },
    )

    def clean_file(self) -> list[list[str]]:
        upload = self.cleaned_data["file"]
        # Refused on its declared size before a byte is read, so a huge upload is never pulled into memory.
        if upload.size > MAX_IMPORT_BYTES:
            raise forms.ValidationError(TOO_LARGE)
        try:
            return read_csv(upload.read())
        except ImportRefused as exc:
            raise forms.ValidationError(str(exc)) from exc


class VendorFloorForm(forms.Form):
    """Space Map Capacity: how many tables fit (blank while the map is not ready) and the mapper's note."""

    capacity = forms.IntegerField(
        min_value=1,
        max_value=VENDOR_CAPACITY_MAX,
        required=False,
        label="Tables that fit",
        widget=forms.NumberInput(
            attrs={"min": "1", "max": str(VENDOR_CAPACITY_MAX), "step": "1", "placeholder": "e.g. 24"}
        ),
    )
    map_note = forms.CharField(
        max_length=200,
        required=False,
        label="Map notes",
        widget=forms.TextInput(attrs={"placeholder": "e.g. 18 in the gallery, 6 in the shop"}),
    )


# A Discord channel id is digits alone (Discord's snowflake).
_CHANNEL_ID = re.compile(r"[0-9]{1,32}")
NOT_A_CHANNEL_ID = "Paste the channel's id: digits only, like 1234567890123456789."


class VendorDiscordChannelForm(forms.Form):
    """The Discord channel Check Discord reads (#1014), by its id, or blank to hide the button."""

    discord_channel_id = forms.CharField(
        max_length=32,
        required=False,
        label="Channel id",
        widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "off"}),
    )

    def clean_discord_channel_id(self) -> str:
        channel_id = self.cleaned_data["discord_channel_id"].strip()
        if channel_id and not _CHANNEL_ID.fullmatch(channel_id):
            raise forms.ValidationError(NOT_A_CHANNEL_ID)
        return channel_id


# Lee's words when the hours are out of range, and when they are not in quarter hours.
HOURS_ERROR = "Enter hours between 0.25 and 24."
HOURS_STEP_ERROR = "Enter hours in quarter hours, like 1.25 or 2.5."


def quarter_hours(hours: Decimal) -> Decimal:
    """``hours`` held to :data:`hq.models.HOURS_MIN` to :data:`hq.models.HOURS_MAX` in quarter hours, at two places.

    The staff time (D6c) uses it, so one bad figure reads as one message in Lee's words.

    Raises:
        ValidationError: :data:`HOURS_ERROR` out of range, :data:`HOURS_STEP_ERROR` off the quarter hour.
    """
    if not HOURS_MIN <= hours <= HOURS_MAX:
        raise ValidationError(HOURS_ERROR)
    if hours % HOURS_STEP:
        raise ValidationError(HOURS_STEP_ERROR)
    # A quarter hour typed with trailing zeros ("2.500") is the same figure; the column holds two places.
    return hours.quantize(Decimal("0.01"))


def _hours_field(placeholder: str) -> forms.DecimalField:
    """The hours box: a quarter hour up to a day, checked by :func:`quarter_hours` for Lee's one message."""
    return forms.DecimalField(
        label="Hours",
        error_messages={"required": HOURS_ERROR, "invalid": HOURS_ERROR},
        widget=forms.NumberInput(
            attrs={
                "min": str(HOURS_MIN),
                "max": str(HOURS_MAX),
                "step": str(HOURS_STEP),
                "inputmode": "decimal",
                "placeholder": placeholder,
            }
        ),
    )


# What an image upload may be, by what Pillow found in the bytes, and the extension it is stored under.
UPLOAD_IMAGE_TYPES: dict[str, str] = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}

# The first bytes of every PDF.
PDF_MAGIC = b"%PDF-"


def _megabytes(limit: int) -> str:
    return f"{limit / (1024 * 1024):.0f}"


def checked_image_type(upload: UploadedFile, types: Mapping[str, str], type_error: str) -> str:
    """The extension of the image in ``upload``, by what Pillow found in the bytes, if that is one of ``types``.

    Opens the bytes with Pillow (:func:`validate_image_content`, which sets the real ``content_type`` and rewinds the
    file), looks the type up in ``types`` (content type to extension), then refuses an image over the pixel cap before
    anything decodes it (:func:`validate_image_pixels`). A file Pillow opens in any other format (a GIF, BMP, TIFF,
    ICO or EPS) is refused like one it cannot open. The name and the declared type never decide.

    Raises:
        ValidationError: ``type_error`` for anything not in ``types``, or the pixel cap's own words.
    """
    try:
        validate_image_content(upload)
    except ValidationError:
        raise ValidationError(type_error) from None
    extension = types.get(upload.content_type or "")
    if extension is None:
        raise ValidationError(type_error)
    validate_image_pixels(upload)
    return extension


_WEB_LINK = [URLValidator(schemes=["http", "https"])]


# ---- PL Ledger, budget (Track D, part 6a) ----

# The largest amount a ledger field takes, in dollars. The cents columns are Postgres integers, so an unbounded figure
# would overflow on save as a server error rather than a form error.
LEDGER_MAX_DOLLARS = 1_000_000

RECEIPT_TYPE_ERROR = "That file is not a receipt we can keep. Use a photo (JPG, PNG, WEBP or HEIC) or a PDF."

# What a receipt photo may be: the three upload images, and a phone's HEIC (Pillow names HEIC and HEIF alike ``image/heif``).
RECEIPT_IMAGE_TYPES: dict[str, str] = {**UPLOAD_IMAGE_TYPES, "image/heif": "heic"}
RECEIPT_NOT_TAKEN = "A sponsored or donated entry takes no receipt."


def checked_receipt(upload: UploadedFile) -> UploadedFile:
    """A receipt as uploaded: a PDF (its first bytes, held to ``MAX_UPLOAD_DOCUMENT_BYTES``) or a photo, held to
    ``MAX_UPLOAD_IMAGE_BYTES`` and checked by :func:`checked_image_type` against :data:`RECEIPT_IMAGE_TYPES` (JPG,
    PNG, WEBP or HEIC, and the pixel cap before anything decodes it). Never the name or the declared type.

    The file is renamed to what was found (``.pdf``, or the photo's format), so the entry tells a PDF from a photo by
    its name: a photo is re-encoded to a JPEG on save, a PDF is stored as it came.

    Raises:
        ValidationError: :data:`RECEIPT_TYPE_ERROR` for anything else, or the cap it is over.
    """
    assert upload.size is not None  # an uploaded file always reports its size
    upload.seek(0)
    head = upload.read(len(PDF_MAGIC))
    upload.seek(0)
    stem = Path(upload.name or "").stem or "receipt"
    if head == PDF_MAGIC:
        if upload.size > settings.MAX_UPLOAD_DOCUMENT_BYTES:
            raise ValidationError(f"A PDF must be {_megabytes(settings.MAX_UPLOAD_DOCUMENT_BYTES)} MB or smaller.")
        upload.name = f"{stem}.pdf"
        upload.content_type = "application/pdf"
        return upload
    validate_image_size(upload)
    upload.name = f"{stem}.{checked_image_type(upload, RECEIPT_IMAGE_TYPES, RECEIPT_TYPE_ERROR)}"
    return upload


def _money(
    label: str, *, minimum: str = "0", maximum: int = LEDGER_MAX_DOLLARS, required: bool = True
) -> forms.DecimalField:
    """A dollars and cents amount from ``minimum`` up to ``maximum`` (:data:`LEDGER_MAX_DOLLARS` unless a field sets a
    smaller cap); the form stores it in cents."""
    return forms.DecimalField(
        min_value=Decimal(minimum),
        max_value=Decimal(maximum),
        max_digits=9,
        decimal_places=2,
        label=label,
        required=required,
        widget=forms.NumberInput(attrs={"min": minimum, "max": str(maximum), "step": "0.01", "inputmode": "decimal"}),
    )


def _cents(dollars: Decimal) -> int:
    return int((dollars * 100).to_integral_value())


def _dollars_initial(cents: int) -> Decimal:
    return (Decimal(cents) / 100).quantize(Decimal("0.01"))


class BudgetLineForm(forms.ModelForm):
    """Add a budget line: Lee's three fields, the budget in dollars and cents."""

    budget = _money("Budgeted amount ($)")

    class Meta:
        model = BudgetLine
        fields = ["name", "category"]
        labels = {"name": "Line name", "category": "Category"}
        widgets = {"name": forms.TextInput(attrs={"placeholder": "e.g. Wine wall tags"})}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if self.instance.pk is not None:
            self.initial.setdefault("budget", _dollars_initial(self.instance.budget_cents))
        for field in self.fields.values():
            field.help_text = ""

    def to_fields(self) -> dict[str, Any]:
        return {
            "name": self.cleaned_data["name"],
            "category": self.cleaned_data["category"],
            "budget_cents": _cents(self.cleaned_data["budget"]),
        }


class BudgetLineEditForm(BudgetLineForm):
    """Edit one budget line, saved with one Save. Each label names the line ("Line name for Kegs"), and the form
    carries the line's ``updated_at`` as :meth:`seen`, so :func:`hq.services.update_budget_line` can refuse a Save
    from a page older than the line."""

    updated_at = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["updated_at"].initial = self._stamp
        for name in ("name", "category", "budget"):
            field = self.fields[name]
            field.label = f"{field.label} for {self.instance.name}"

    @property
    def _stamp(self) -> str:
        return self.instance.updated_at.isoformat()

    def seen(self) -> str:
        """The line's ``updated_at`` as this page was rendered with it."""
        return str(self.cleaned_data["updated_at"])


def _receipt_field() -> forms.FileField:
    return forms.FileField(
        required=False,
        label="Receipt photo or PDF",
        widget=forms.FileInput(attrs={"accept": "image/*,application/pdf"}),
    )


class LedgerEntryForm(forms.ModelForm):
    """Log a sponsor or expense: what happened (Lee's four kinds, the approval named in their labels), the budget line,
    who it came from or went to, the value or amount, the date, a note and, for money spent or refunded, the receipt.

    The line is one of the hub's lines (``lines``, read once by the view). The receipt goes through
    :func:`checked_receipt`; a sponsored entry that brings one is refused.
    """

    line = forms.TypedChoiceField(coerce=int, label="Budget line")
    amount = _money("Amount ($)", minimum="0.01")
    receipt = _receipt_field()

    class Meta:
        model = LedgerEntry
        fields = ["kind", "line", "source", "date", "note", "receipt"]
        labels = {"kind": "What happened?", "source": "Sponsor or vendor", "date": "Date", "note": "Note (optional)"}
        widgets = {
            "kind": forms.RadioSelect(attrs={"x-model": "kind"}),
            "source": forms.TextInput(attrs={"placeholder": "e.g. Cascade Brewing"}),
            "date": forms.DateInput(format="%Y-%m-%d", attrs=_DATE_PICKER),
            "note": forms.TextInput(attrs={"placeholder": "e.g. 1 half-barrel IPA, in-kind"}),
        }

    def __init__(self, *args: Any, lines: Sequence[BudgetLine], approval: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._lines = {line.pk: line for line in lines}
        self.fields["line"].choices = [(line.pk, line.name) for line in lines]  # type: ignore[attr-defined]  # a TypedChoiceField
        self.fields["kind"].choices = [  # type: ignore[attr-defined]  # a ChoiceField
            (kind.value, str(kind.label).replace("$900", approval)) for kind in LedgerKind
        ]
        for field in self.fields.values():
            field.help_text = ""

    def clean_line(self) -> BudgetLine:
        pk: int = self.cleaned_data["line"]
        return self._lines[pk]

    def clean_receipt(self) -> UploadedFile | None:
        upload: UploadedFile | None = self.cleaned_data["receipt"]
        if not upload:
            return None
        return checked_receipt(upload)

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        if cleaned.get("receipt") and cleaned.get("kind") not in RECEIPT_KINDS:
            self.add_error("receipt", RECEIPT_NOT_TAKEN)
        return cleaned

    def to_fields(self) -> dict[str, Any]:
        """The entry's fields for ``hq.services.add_ledger_entry``: the amount in cents, the receipt when one came."""
        fields = {name: self.cleaned_data[name] for name in ("kind", "line", "source", "date", "note")}
        fields["amount_cents"] = _cents(self.cleaned_data["amount"])
        if self.cleaned_data["receipt"]:
            fields["receipt"] = self.cleaned_data["receipt"]
        return fields


class ReceiptForm(forms.Form):
    """Add a receipt to an entry that needs one, from its ledger row. Prefixed by the entry, so each row's form keeps
    its own ids."""

    receipt = forms.FileField(
        label="Receipt photo or PDF",
        widget=forms.FileInput(attrs={"accept": "image/*,application/pdf"}),
    )

    def __init__(self, *args: Any, entry: LedgerEntry, **kwargs: Any) -> None:
        kwargs["prefix"] = f"receipt-{entry.pk}"
        super().__init__(*args, **kwargs)
        self.entry = entry
        self.fields["receipt"].label = f"Receipt for {entry.source} on {entry.date:%b} {entry.date.day}"

    def clean_receipt(self) -> UploadedFile:
        return checked_receipt(self.cleaned_data["receipt"])


# ---- PL Ledger, money in (Track D, part 6b) ----

# The highest price a drink takes, in dollars: far above any bar's, low enough that a price times
# the most drinks a batch takes still fits the cents column.
PRICE_MAX_DOLLARS = 1_000
BAR_TOTAL_TOO_BIG = "That comes to more than $1,000,000. Enter the total collected."


def _count(label: str, *, maximum: int, minimum: int = 0, initial: int | None = None) -> forms.IntegerField:
    return forms.IntegerField(
        min_value=minimum,
        max_value=maximum,
        label=label,
        initial=initial,
        widget=forms.NumberInput(attrs={"min": str(minimum), "max": str(maximum), "step": "1", "inputmode": "numeric"}),
    )


class _SeenText(forms.CharField):
    """A seen text figure (the approval's note), posted as is: blank stays blank so a blank note still matches, and
    only a missing field is None, which never matches."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(required=False, strip=False, widget=forms.HiddenInput, **kwargs)

    def to_python(self, value: Any) -> str | None:
        return None if value is None else str(value)


class _SeenFigures(forms.Form):
    """A settings form that carries the figures it was rendered with as hidden ``seen_*`` fields, so a save from an
    older page is refused (``hq.services.save_ledger_figures``) rather than overwriting what someone else saved.

    ``FIGURES`` names the whole number settings fields the form writes and ``TEXT_FIGURES`` the text ones (the
    approval's note); each visible field's initial comes from them through :meth:`initial_for`.
    """

    FIGURES: tuple[str, ...] = ()
    TEXT_FIGURES: tuple[str, ...] = ()

    def __init__(self, *args: Any, found: LedgerSettings, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for name in self.FIGURES:
            self.fields[f"seen_{name}"] = forms.IntegerField(
                required=False, widget=forms.HiddenInput, initial=getattr(found, name)
            )
        for name in self.TEXT_FIGURES:
            self.fields[f"seen_{name}"] = _SeenText(initial=getattr(found, name))

    def _seen_names(self) -> tuple[str, ...]:
        return (*self.FIGURES, *self.TEXT_FIGURES)

    def seen(self) -> dict[str, int | str | None]:
        """The figures this form was rendered with, as posted (None when missing, which never matches)."""
        return {name: self.cleaned_data.get(f"seen_{name}") for name in self._seen_names()}

    def seen_fields(self) -> list[forms.BoundField]:
        return [self[f"seen_{name}"] for name in self._seen_names()]


class LedgerApprovalForm(_SeenFigures):
    """The board's approval: the amount and the line under it (Lee's "Drinks budget, Oct 14 meeting").

    It carries the approval it was rendered with, as the D6b settings forms carry theirs, so only someone else's
    approval save makes it stale: a price save in between does not."""

    FIGURES = ("approved_cents",)
    TEXT_FIGURES = ("approved_note",)

    approved = _money("Board approval ($)")
    approved_note = forms.CharField(
        max_length=120,
        required=False,
        label="Note",
        widget=forms.TextInput(attrs={"placeholder": "e.g. Drinks budget, Oct 14 meeting"}),
    )

    @classmethod
    def initial_for(cls, found: LedgerSettings) -> dict[str, Any]:
        return {"approved": _dollars_initial(found.approved_cents), "approved_note": found.approved_note}

    def to_fields(self) -> dict[str, Any]:
        return {
            "approved_cents": _cents(self.cleaned_data["approved"]),
            "approved_note": self.cleaned_data["approved_note"],
        }


class DrinkPricesForm(_SeenFigures):
    """Lee's Set drink prices: the four drinks' prices and what the bar sold in 2025, in dollars and cents."""

    FIGURES = (*DRINK_PRICE_FIELDS.values(), "last_year_bar_cents")

    ipa = _money("IPA ($)", maximum=PRICE_MAX_DOLLARS)
    pils = _money("Pilsner ($)", maximum=PRICE_MAX_DOLLARS)
    wine = _money("Wine ($)", maximum=PRICE_MAX_DOLLARS)
    na = _money("N/A drink ($)", maximum=PRICE_MAX_DOLLARS)
    last_year = _money("2025 bar sales ($)")

    @classmethod
    def initial_for(cls, found: LedgerSettings) -> dict[str, Any]:
        initial = {drink: _dollars_initial(drink_price_cents(found, drink)) for drink in PRICED_DRINKS}
        return {**initial, "last_year": _dollars_initial(found.last_year_bar_cents)}

    def to_fields(self) -> dict[str, int]:
        fields = {DRINK_PRICE_FIELDS[drink]: _cents(self.cleaned_data[drink]) for drink in PRICED_DRINKS}
        return {**fields, "last_year_bar_cents": _cents(self.cleaned_data["last_year"])}


class BarQuickForm(forms.Form):
    """One +1 tap: the drink (the button pressed) and Paid by (Lee's four, chosen once above the buttons)."""

    drink = forms.ChoiceField(choices=[(drink, BarDrink(drink).label) for drink in PRICED_DRINKS])
    pay = forms.ChoiceField(
        choices=BarPay.choices, initial=BarPay.CARD, label="Paid by", widget=forms.RadioSelect, help_text=""
    )


class BarBatchForm(forms.Form):
    """Lee's Log a batch (end of shift, tip jar, cash box count): the drink (Mixed for a total only), how many, the
    total collected, Paid by and a note.

    A blank total is the drink's price times how many; a mixed batch has no price, so it needs its total. A batch with
    neither drinks nor money is refused in Lee's words.
    """

    drink = forms.ChoiceField(choices=BarDrink.choices, label="Drink", initial=BarDrink.IPA)
    qty = _count("How many", maximum=BAR_QTY_MAX, initial=1)
    total = _money("Total collected ($)", required=False)
    pay = forms.ChoiceField(choices=BarPay.choices, label="Paid by", initial=BarPay.CARD)
    note = forms.CharField(
        max_length=200,
        required=False,
        label="Note (optional)",
        widget=forms.TextInput(attrs={"placeholder": "e.g. 6 to 8 PM shift, Augustin"}),
    )

    def __init__(self, *args: Any, found: LedgerSettings, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._found = found
        self.fields["total"].widget.attrs["placeholder"] = "Leave blank to use price × qty"

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        drink, qty, total = cleaned.get("drink"), cleaned.get("qty"), cleaned.get("total")
        if drink is None or qty is None or self.has_error("total"):
            return cleaned
        if total is None:
            if drink == BarDrink.MIXED:
                self.add_error("total", BAR_MIXED_NEEDS_TOTAL)
                return cleaned
            amount = qty * drink_price_cents(self._found, drink)
        else:
            amount = _cents(total)
        if qty == 0 and amount == 0:
            self.add_error(None, BAR_NOTHING_ENTERED)
        elif amount > BAR_AMOUNT_MAX_CENTS:
            self.add_error("total", BAR_TOTAL_TOO_BIG)
        cleaned["amount_cents"] = amount
        return cleaned

    def to_fields(self) -> dict[str, Any]:
        """The sale's fields for ``hq.services.add_bar_batch``: the amount in cents, defaulted from the price."""
        return {
            "drink": self.cleaned_data["drink"],
            "qty": self.cleaned_data["qty"],
            "amount_cents": self.cleaned_data["amount_cents"],
            "pay": self.cleaned_data["pay"],
            "note": self.cleaned_data["note"],
        }


# ---- PL Ledger, team and tally (Track D, part 6c) ----

# The datalist the launch checklist's Owner, the staff time's Who and the guild demos' Contacted by suggest from: the
# guild officers.
LEDGER_TEAM_LIST_ID = "hq-lg-team"


class DoorCountForm(forms.Form):
    """One tap on the door clicker: the button pressed, +1, +5, +10 or Undo 1 (-1)."""

    delta = forms.TypedChoiceField(coerce=int, choices=[(str(step), f"{step:+d}") for step in DOOR_STEPS])


class _RowStamp(forms.ModelForm):
    """A row's edit form that carries the row's ``updated_at`` as the page saw it and ties every control to the row's
    ``<form>`` by its ``form`` attribute (``form_id``), since a form cannot wrap a table row. Each label names the row
    (``row_name``). :meth:`seen` is the stamp its service holds a Save to, refusing one from a page older than the
    row."""

    updated_at = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args: Any, form_id: str, row_name: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.form_id = form_id
        self.fields["updated_at"].initial = self._stamp
        for name in self._meta.fields or ():
            field = self.fields[name]
            field.help_text = ""
            field.widget.attrs["form"] = form_id
            field.label = f"{field.label} for {row_name}"

    @property
    def _stamp(self) -> str:
        return self.instance.updated_at.isoformat()

    def seen(self) -> str:
        """The row's ``updated_at`` as this page was rendered with it."""
        return str(self.cleaned_data["updated_at"])

    def to_fields(self) -> dict[str, Any]:
        return {name: self.cleaned_data[name] for name in self._meta.fields or ()}


_TEAM_INPUT = {"list": LEDGER_TEAM_LIST_ID, "autocomplete": "off"}


class LaunchTaskForm(forms.ModelForm):
    """Add a task: Lee's three fields, the task, its owner (the team suggested) and when it is due."""

    class Meta:
        model = LaunchTask
        fields = ["task", "owner", "due"]
        labels = {"task": "Task", "owner": "Owner", "due": "Due"}
        widgets = {
            "task": forms.TextInput(attrs={"placeholder": "e.g. Post on Nextdoor"}),
            "owner": forms.TextInput(attrs={**_TEAM_INPUT, "placeholder": "e.g. Amy"}),
            "due": forms.DateInput(format="%Y-%m-%d", attrs=_DATE_PICKER),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.help_text = ""

    def to_fields(self) -> dict[str, Any]:
        return {name: self.cleaned_data[name] for name in self._meta.fields or ()}


class LaunchTaskEditForm(_RowStamp):
    """One line of the launch checklist, saved with one Save: the task, owner, status, due date and the link once
    live (http or https)."""

    link = forms.URLField(
        max_length=500,
        required=False,
        label="Link",
        validators=_WEB_LINK,
        widget=forms.URLInput(attrs={"placeholder": "Paste link when live"}),
    )

    class Meta:
        model = LaunchTask
        fields = ["task", "owner", "status", "due", "link"]
        labels = {"task": "Task", "owner": "Owner", "status": "Status", "due": "Due"}
        widgets = {
            "owner": forms.TextInput(attrs={**_TEAM_INPUT, "placeholder": "Who"}),
            "due": forms.DateInput(format="%Y-%m-%d", attrs=_DATE_PICKER),
        }


class StaffTimeForm(forms.ModelForm):
    """Log staff time: Lee's five fields. Who suggests the team; the hours are a quarter hour up to a day."""

    hours = _hours_field("e.g. 1.5")

    class Meta:
        model = StaffTime
        fields = ["who", "date", "hours", "area", "note"]
        labels = {"who": "Who", "date": "Date", "area": "Work area", "note": "What you did"}
        widgets = {
            "who": forms.TextInput(attrs={**_TEAM_INPUT, "placeholder": "e.g. Lee"}),
            "date": forms.DateInput(format="%Y-%m-%d", attrs=_DATE_PICKER),
            "note": forms.TextInput(attrs={"placeholder": "e.g. Built the ledger and sponsorship tracker"}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.help_text = ""

    def clean_hours(self) -> Decimal:
        return quarter_hours(self.cleaned_data["hours"])

    def to_fields(self) -> dict[str, Any]:
        return {name: self.cleaned_data[name] for name in self._meta.fields or ()}

    def kept_for_next(self) -> dict[str, Any]:
        """What the next entry starts with after a save: the same who, date and area; the hours and note clear."""
        return {key: self.cleaned_data[key] for key in ("who", "date", "area")}


class GuildDemoAddForm(forms.Form):
    """Add a guild: one of the active guilds not on the list yet (``guilds``, read once by the view) and its lead,
    which defaults to the guild's own lead when left blank."""

    guild = forms.TypedChoiceField(coerce=int, label="Guild")
    lead = forms.CharField(
        max_length=120,
        required=False,
        label="Guild lead",
        widget=forms.TextInput(attrs={"placeholder": "Their guild lead if blank"}),
    )

    def __init__(self, *args: Any, guilds: Sequence[Guild], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._guilds = {guild.pk: guild for guild in guilds}
        self.fields["guild"].choices = [(guild.pk, guild.name) for guild in guilds]  # type: ignore[attr-defined]  # a TypedChoiceField

    def clean_guild(self) -> Guild:
        pk: int = self.cleaned_data["guild"]
        return self._guilds[pk]


class GuildDemoEditForm(_RowStamp):
    """One guild on the list, saved with one Save: its lead, whether it was contacted and by whom, whether it demos,
    and what."""

    class Meta:
        model = GuildDemo
        fields = ["lead", "contacted", "contacted_by", "demo", "note"]
        labels = {
            "lead": "Guild lead",
            "contacted": "Contacted",
            "contacted_by": "Contacted by",
            "demo": "Demo planned",
            "note": "What they'll demo",
        }
        widgets = {
            "lead": forms.TextInput(attrs={"placeholder": "Name"}),
            "contacted_by": forms.TextInput(attrs={**_TEAM_INPUT, "placeholder": "Who reached out"}),
            "note": forms.TextInput(attrs={"placeholder": "What they'll demo"}),
        }
