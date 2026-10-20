"""The HQ forms: the hub with its goals, one card with its shifts, a volunteer's Your Info step, the wine wall's Log a
Bottle and winery logo, the Marketing Tracker's Log a Marketing Action, the Sponsorship Tracker's add form, inline
edit and contact log line, Vendor Tables' add form, row edit and space map capacity, and the Market Check-in's report
and hours."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pathlib import Path

from django import forms
from django.conf import settings
from django.core.validators import URLValidator
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.db.models import Prefetch
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils import timezone

from core.validators import validate_image_content, validate_image_pixels, validate_image_size
from hq.models import (
    EventHub,
    FlyerSlot,
    HubCard,
    HubFlyer,
    HOURS_MAX,
    HOURS_MIN,
    HOURS_STEP,
    HubGoal,
    MarketingAction,
    MarketReport,
    ShiftSignup,
    Sponsor,
    SponsorTier,
    VendorTable,
    VolunteerHours,
    VolunteerShift,
    WineBottle,
)
from hq.services import VENDOR_CAPACITY_MAX, VolunteerInfo
from membership.models import CommunityEvent, Guild, Member

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


class WineBottleForm(forms.ModelForm):
    """Log a Bottle on the wine wall: Lee's twelve fields, with the value typed in dollars.

    ``website`` is the same honeypot as :class:`VolunteerInfoForm`'s: hidden from people and
    labelled to be left blank, so a bot that fills every field gives itself away.
    """

    value = forms.DecimalField(
        min_value=Decimal(0),
        max_digits=8,
        decimal_places=2,
        label="Value per bottle ($)",
        help_text="What one bottle is worth, in dollars.",
    )
    qty = forms.IntegerField(
        min_value=1, initial=1, label="How many", help_text="How many of this bottle came in, at least 1."
    )
    website = forms.CharField(
        required=False,
        label="Leave this blank",
        widget=forms.TextInput(attrs={"autocomplete": "off", "tabindex": "-1"}),
    )

    class Meta:
        model = WineBottle
        fields = [
            "kind",
            "name",
            "winery",
            "qty",
            "giver_name",
            "giver_type",
            "giver_email",
            "listing",
            "credited_name",
            "photo",
            "note",
        ]
        labels = {
            "kind": "Type",
            "name": "Bottle",
            "winery": "Winery, cidery or brewery",
            "giver_name": "Given by",
            "giver_type": "Giver type",
            "giver_email": "Giver's email",
            "listing": "List them on the sponsor page?",
            "credited_name": "Credited volunteer",
            "photo": "Photo",
            "note": "Note",
        }
        help_texts = {
            "winery": "As on the label. Bottles group under it on the wall.",
            "giver_email": "Optional. Stays private to the organisers, for the thank you note.",
            "credited_name": "Who collected it. Starts as your name.",
            "note": "Optional.",
        }
        widgets = {
            "giver_email": forms.EmailInput(attrs={"autocomplete": "email"}),
            "note": forms.Textarea(attrs={"rows": 2}),
        }

    def clean_photo(self) -> Any:
        """A fresh upload must be an image we can open, under the size cap; none is fine."""
        photo = self.cleaned_data["photo"]
        if isinstance(photo, UploadedFile):
            validate_image_size(photo)
            validate_image_content(photo)
        return photo

    @property
    def is_honeypot_hit(self) -> bool:
        """Whether the hidden field was filled, which only a bot does."""
        return bool(self.cleaned_data["website"])

    def to_fields(self) -> dict[str, Any]:
        """The bottle's fields for ``hq.services.log_bottle``: the cleaned data less the honeypot, the value in cents."""
        fields = dict(self.cleaned_data)
        del fields["website"]
        value: Decimal = fields.pop("value")
        fields["value_cents"] = int((value * 100).to_integral_value())
        for key in ("name", "winery", "giver_name", "credited_name", "note"):
            fields[key] = fields[key].strip()
        return fields


class WineryLogoForm(forms.Form):
    """The organisers' logo upload: a winery already named on the wall, and its logo."""

    name = forms.ChoiceField(label="Winery", help_text="A winery, cidery or brewery with a bottle on the wall.")
    logo = forms.ImageField(label="Logo")

    def __init__(self, *args: Any, names: Sequence[str], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["name"].choices = [(name, name) for name in names]  # type: ignore[attr-defined]  # a ChoiceField

    def clean_logo(self) -> UploadedFile:
        """The form's ImageField has opened it; this holds it under the size cap too."""
        logo: UploadedFile = self.cleaned_data["logo"]
        validate_image_size(logo)
        return logo


# The datalist the Who field suggests from: the Marketing card's named members.
MARKETING_TEAM_LIST_ID = "hq-marketing-team"


class MarketingActionForm(forms.ModelForm):
    """Log a Marketing Action: Lee's six fields. Views or reach is optional and stored as 0 when left blank."""

    reach = forms.IntegerField(
        min_value=0,
        required=False,
        label="Views or reach",
        widget=forms.NumberInput(attrs={"min": "0", "step": "1", "placeholder": "optional"}),
    )

    class Meta:
        model = MarketingAction
        fields = ["kind", "date", "who", "reach", "details", "link"]
        labels = {"kind": "What", "date": "Date", "who": "Who", "details": "Details", "link": "Link"}
        help_texts = {
            "kind": "",
            "date": "",
            "who": "",
            "details": "",
            "link": "",
        }
        widgets = {
            # The whole field opens the picker, not just its icon (FRONTEND rule 14).
            "date": forms.DateInput(
                format="%Y-%m-%d",
                attrs={"type": "date", "@click": "(() => { try { $el.showPicker() } catch (e) {} })()"},
            ),
            "who": forms.TextInput(
                attrs={"list": MARKETING_TEAM_LIST_ID, "placeholder": "e.g. Anjali", "autocomplete": "off"}
            ),
            "details": forms.TextInput(attrs={"placeholder": "e.g. Reel: Metal Guild sparks demo teaser"}),
            "link": forms.URLInput(attrs={"placeholder": "optional"}),
        }

    def clean_reach(self) -> int:
        reach: int | None = self.cleaned_data["reach"]
        return 0 if reach is None else reach

    def kept_for_next(self) -> dict[str, Any]:
        """What the next entry starts with after a save: the same kind, date and who; the rest clears."""
        return {key: self.cleaned_data[key] for key in ("kind", "date", "who")}


# ---- Sponsorship Tracker (Track D, part 2) ----

# The datalist Who's asking and the log's By suggest from: the Sponsorship card's named members.
SPONSOR_TEAM_LIST_ID = "hq-sponsor-team"

# The whole field opens the date picker, not just its icon (FRONTEND rule 14).
_DATE_PICKER = {"type": "date", "@click": "(() => { try { $el.showPicker() } catch (e) {} })()"}


# The largest whole dollar amount a sponsor field takes. The cents columns are Postgres integers (at most
# 2,147,483,647), so an unbounded figure would overflow on save as a server error rather than a form error.
SPONSOR_MAX_DOLLARS = 1_000_000


def _dollars(label: str) -> forms.IntegerField:
    """A whole dollar amount up to :data:`SPONSOR_MAX_DOLLARS`, blank meaning none; the form stores it in cents."""
    return forms.IntegerField(
        min_value=0,
        max_value=SPONSOR_MAX_DOLLARS,
        required=False,
        label=label,
        widget=forms.NumberInput(attrs={"min": "0", "step": "1"}),
    )


class SponsorForm(forms.ModelForm):
    """+ Add a Sponsor: Lee's eleven fields, the ask in whole dollars. Every new sponsor starts as a prospect."""

    ask = _dollars("Ask amount ($)")

    class Meta:
        model = Sponsor
        fields = [
            "organization",
            "tier",
            "owner",
            "contact_name",
            "contact_role",
            "email",
            "phone",
            "next_step",
            "follow_up_by",
            "fit_note",
        ]
        labels = {
            "organization": "Business or organization",
            "tier": "Tier",
            "owner": "Who's asking",
            "contact_name": "Contact name",
            "contact_role": "Their role",
            "email": "Email",
            "phone": "Phone",
            "next_step": "Next step",
            "follow_up_by": "Follow up by",
            "fit_note": "Why they're a fit",
        }
        widgets: dict[str, forms.Widget] = {
            "organization": forms.TextInput(attrs={"placeholder": "e.g. OnPoint Community Credit Union"}),
            "owner": forms.TextInput(
                attrs={"list": SPONSOR_TEAM_LIST_ID, "placeholder": "e.g. Augustin", "autocomplete": "off"}
            ),
            "contact_role": forms.TextInput(attrs={"placeholder": "e.g. Branch manager"}),
            "email": forms.EmailInput(attrs={"autocomplete": "off"}),
            "phone": forms.TextInput(attrs={"type": "tel", "autocomplete": "off"}),
            "next_step": forms.TextInput(attrs={"placeholder": "e.g. Send sponsor deck"}),
            "follow_up_by": forms.DateInput(format="%Y-%m-%d", attrs=_DATE_PICKER),
            "fit_note": forms.TextInput(attrs={"placeholder": "e.g. PLM and 16 guild accounts bank with them"}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["tier"].choices = [("", "Not set"), *SponsorTier.choices]  # type: ignore[attr-defined]  # a ChoiceField
        for field in self.fields.values():
            field.help_text = ""

    def to_fields(self) -> dict[str, Any]:
        """The sponsor's fields for ``hq.services``: the cleaned data with each dollar amount in cents."""
        fields = {name: self.cleaned_data[name] for name in self._meta.fields or ()}
        for dollars, cents in self.MONEY:
            amount: int | None = self.cleaned_data[dollars]
            fields[cents] = 0 if amount is None else amount * 100
        return fields

    # The dollar fields and the model fields they fill.
    MONEY: tuple[tuple[str, str], ...] = (("ask", "ask_cents"),)


class SponsorEditForm(SponsorForm):
    """The inline edit on a pipeline row: the add form's fields plus the status, the money in, the thank-you,
    what was promised, the notes and last year's gift. Built with ``initial_for(sponsor)``, never bound to the
    row itself, so the old status is still on the row when ``hq.services.update_sponsor`` compares."""

    ask = _dollars("Ask ($)")
    amount = _dollars("Committed or received ($)")

    MONEY = (("ask", "ask_cents"), ("amount", "amount_cents"))

    class Meta(SponsorForm.Meta):
        fields = [*SponsorForm.Meta.fields, "status", "thanked", "benefits", "note", "history"]
        labels = {
            **SponsorForm.Meta.labels,
            "contact_role": "Role",
            "status": "Status",
            "thanked": "Thank-you sent",
            "benefits": "Benefits promised",
            "note": "Notes",
            "history": "Last year (2025)",
        }
        widgets: dict[str, forms.Widget] = {
            **SponsorForm.Meta.widgets,
            "organization": forms.TextInput(),
            "contact_role": forms.TextInput(),
            "next_step": forms.TextInput(),
            "fit_note": forms.TextInput(),
            "benefits": forms.TextInput(attrs={"placeholder": "e.g. Logo on flyer, 4 drink tickets"}),
            "note": forms.Textarea(attrs={"rows": 2}),
            "history": forms.Textarea(attrs={"rows": 2, "placeholder": "What they gave in 2025, if anything"}),
        }

    @classmethod
    def initial_for(cls, sponsor: Sponsor) -> dict[str, Any]:
        """What the row's form starts with: the sponsor's fields, the money in whole dollars."""
        initial: dict[str, Any] = {name: getattr(sponsor, name) for name in cls._meta.fields or ()}
        initial["ask"] = sponsor.ask_cents // 100 or None
        initial["amount"] = sponsor.amount_cents // 100 or None
        return initial


class SponsorLogForm(forms.Form):
    """One line for a sponsor's contact log: what happened, and who."""

    text = forms.CharField(
        max_length=300,
        label="What happened",
        widget=forms.TextInput(attrs={"placeholder": "e.g. Called, left voicemail"}),
    )
    by = forms.CharField(
        max_length=120,
        required=False,
        label="By",
        widget=forms.TextInput(attrs={"list": SPONSOR_TEAM_LIST_ID, "placeholder": "By", "autocomplete": "off"}),
    )


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
    """One row of the list: every field of the add form plus the status and the table number, saved with one Save.

    Rendered across the row's cells, each control tied to the row's form by its ``form`` attribute
    (``form_id``), since a form cannot wrap a table row. Each label names the row ("Email for Riverbend
    Ceramics"), so a screen reader moving through the cells hears whose field it is in.

    ``updated_at`` carries the row's stamp as the page saw it; :attr:`is_stale` is a Save from a page older
    than the row, which the view refuses whole.
    """

    updated_at = forms.CharField(required=False, widget=forms.HiddenInput)

    class Meta(VendorTableForm.Meta):
        fields = [*VendorTableForm.Meta.fields, "status", "table_no"]
        labels = {**VendorTableForm.Meta.labels, "status": "Status", "table_no": "Table #"}
        widgets: dict[str, forms.Widget] = {
            **VendorTableForm.Meta.widgets,
            "name": forms.TextInput(),
            "sells": forms.TextInput(attrs={"placeholder": "What"}),
            "needs": forms.TextInput(attrs={"placeholder": "Needs"}),
            "email": forms.EmailInput(attrs={"autocomplete": "off", "placeholder": "Email"}),
            "phone": forms.TextInput(attrs={"type": "tel", "autocomplete": "off", "placeholder": "Phone"}),
            "table_no": forms.TextInput(attrs={"placeholder": "#"}),
        }

    def __init__(self, *args: Any, form_id: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.form_id = form_id
        self.fields["updated_at"].initial = self._stamp
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


# ---- Market Check-in (Track D, part 4) ----

# The most a report takes. The columns are Postgres integers, so an unbounded figure would overflow on save as a
# server error rather than a form error; these are far past any table at the market.
REPORT_ITEMS_MAX = 100_000
REPORT_SALES_MAX_DOLLARS = 1_000_000


class MarketReportForm(forms.ModelForm):
    """Artists: your market report. Lee's five fields, the sales in dollars and cents; the guild is one of the active
    guilds or none. Bound to the member's report when they have one (Update my report), else to a new one."""

    guild = forms.TypedChoiceField(required=False, coerce=int, empty_value=None, label="Your guild")
    items_sold = forms.IntegerField(
        min_value=0,
        max_value=REPORT_ITEMS_MAX,
        required=False,
        label="Items sold",
        widget=forms.NumberInput(
            attrs={
                "min": "0",
                "max": str(REPORT_ITEMS_MAX),
                "step": "1",
                "inputmode": "numeric",
                "placeholder": "After the market",
            }
        ),
    )
    sales = forms.DecimalField(
        min_value=Decimal(0),
        max_value=Decimal(REPORT_SALES_MAX_DOLLARS),
        max_digits=9,
        decimal_places=2,
        required=False,
        label="Total sales ($)",
        widget=forms.NumberInput(
            attrs={
                "min": "0",
                "max": str(REPORT_SALES_MAX_DOLLARS),
                "step": "0.01",
                "inputmode": "decimal",
                "placeholder": "After the market",
            }
        ),
    )

    class Meta:
        model = MarketReport
        fields = ["name", "guild", "items_sold", "experience"]
        labels = {"name": "Your name or shop name", "experience": "Your experience at the market, in a sentence"}
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. Riverbend Ceramics"}),
            "experience": forms.TextInput(
                attrs={"placeholder": "e.g. Steady crowd all night, and two people asked about classes!"}
            ),
        }

    def __init__(self, *args: Any, guilds: Sequence[Guild], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._guilds = _offer_guilds(self, guilds)
        if self.instance.pk is not None and self.instance.sales_cents:
            self.initial.setdefault("sales", (Decimal(self.instance.sales_cents) / 100).quantize(Decimal("0.01")))
        for field in self.fields.values():
            field.help_text = ""

    def clean_guild(self) -> Guild | None:
        pk: int | None = self.cleaned_data["guild"]
        return None if pk is None else self._guilds[pk]

    def to_fields(self) -> dict[str, Any]:
        """The report's fields for ``hq.services.save_report``: the cleaned data with the sales in cents."""
        fields = {name: self.cleaned_data[name] for name in self._meta.fields or ()}
        sales: Decimal | None = self.cleaned_data["sales"]
        fields["sales_cents"] = 0 if sales is None else int((sales * 100).to_integral_value())
        return fields


# Lee's words when the hours are out of range, and when they are not in quarter hours.
HOURS_ERROR = "Enter hours between 0.25 and 24."
HOURS_STEP_ERROR = "Enter hours in quarter hours, like 1.25 or 2.5."


class VolunteerHoursForm(forms.ModelForm):
    """Volunteers: log your hours. The date (Portland today), the hours in quarter hours, the task and a note.

    The hours are held to :data:`hq.models.HOURS_MIN` to :data:`hq.models.HOURS_MAX` in ``clean_hours`` rather
    than by the field's own validators, so one bad figure reads as one message, in Lee's words.
    """

    hours = forms.DecimalField(
        label="Hours",
        error_messages={"required": HOURS_ERROR, "invalid": HOURS_ERROR},
        widget=forms.NumberInput(
            attrs={
                "min": str(HOURS_MIN),
                "max": str(HOURS_MAX),
                "step": str(HOURS_STEP),
                "inputmode": "decimal",
                "placeholder": "e.g. 2.5",
            }
        ),
    )

    class Meta:
        model = VolunteerHours
        fields = ["date", "hours", "task", "note"]
        labels = {"date": "Date", "task": "What you worked on", "note": "Note (optional)"}
        widgets = {
            "date": forms.DateInput(format="%Y-%m-%d", attrs=_DATE_PICKER),
            "note": forms.TextInput(attrs={"placeholder": "e.g. Dropped flyers on Division St"}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.help_text = ""

    def clean_hours(self) -> Decimal:
        hours: Decimal = self.cleaned_data["hours"]
        if not HOURS_MIN <= hours <= HOURS_MAX:
            raise ValidationError(HOURS_ERROR)
        if hours % HOURS_STEP:
            raise ValidationError(HOURS_STEP_ERROR)
        # A quarter hour typed with trailing zeros ("2.500") is the same figure; the column holds two places.
        return hours.quantize(Decimal("0.01"))

    def kept_for_next(self) -> dict[str, Any]:
        """What the next entry starts with after a save: the same date and task; the hours and note clear."""
        return {key: self.cleaned_data[key] for key in ("date", "task")}


# ---- Event flyer and sharing (Track D, part 5) ----

FLYER_TYPE_ERROR = "That file is not a flyer we can show. Choose a JPG, PNG or WEBP image, or a PDF."

# What an image upload may be, by what Pillow found in the bytes, and the extension it is stored under.
FLYER_IMAGE_TYPES: dict[str, str] = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}

# The first bytes of every PDF.
PDF_MAGIC = b"%PDF-"


def _megabytes(limit: int) -> str:
    return f"{limit / (1024 * 1024):.0f}"


class FlyerUploadForm(forms.Form):
    """Upload or replace one flyer: an image (JPG, PNG, WEBP; up to ``MAX_UPLOAD_IMAGE_BYTES``) or a PDF (up to
    ``MAX_UPLOAD_DOCUMENT_BYTES``), told apart by the bytes, never the name or the declared type. An image over
    ``core.validators.MAX_IMAGE_PIXELS`` is refused before it is decoded (:func:`validate_image_pixels`).

    The cleaned file is renamed to the type found (``.pdf``, or the image's own extension), so the model can tell a
    PDF from an image by its name: an image is re-encoded on save, a PDF is stored as it came. ``slot`` is the
    :class:`hq.models.FlyerSlot` the form is for; it names the field and prefixes it, so the two forms on the page
    keep distinct ids.
    """

    file = forms.FileField()

    def __init__(self, *args: Any, slot: str, **kwargs: Any) -> None:
        kwargs["prefix"] = str(slot)
        super().__init__(*args, **kwargs)
        self.slot = slot
        self.fields["file"].label = FlyerSlot(slot).label

    def clean_file(self) -> UploadedFile:
        upload: UploadedFile = self.cleaned_data["file"]
        assert upload.size is not None  # an uploaded file always reports its size
        upload.seek(0)
        head = upload.read(len(PDF_MAGIC))
        upload.seek(0)
        stem = Path(upload.name or "").stem or "flyer"
        if head == PDF_MAGIC:
            if upload.size > settings.MAX_UPLOAD_DOCUMENT_BYTES:
                raise ValidationError(f"A PDF must be {_megabytes(settings.MAX_UPLOAD_DOCUMENT_BYTES)} MB or smaller.")
            upload.name = f"{stem}.pdf"
            upload.content_type = "application/pdf"
            return upload
        if upload.size > settings.MAX_UPLOAD_IMAGE_BYTES:
            raise ValidationError(f"An image must be {_megabytes(settings.MAX_UPLOAD_IMAGE_BYTES)} MB or smaller.")
        try:
            # Opens the bytes with Pillow, sets the real content_type and rewinds the file.
            forms.ImageField().to_python(upload)
        except ValidationError:
            raise ValidationError(FLYER_TYPE_ERROR) from None
        extension = FLYER_IMAGE_TYPES.get(upload.content_type or "")
        if extension is None:
            raise ValidationError(FLYER_TYPE_ERROR)
        validate_image_pixels(upload)
        upload.name = f"{stem}.{extension}"
        return upload


_WEB_LINK = [URLValidator(schemes=["http", "https"])]


class FlyerLinksForm(forms.ModelForm):
    """Update this page: the website and Eventbrite links (http or https, blank leaves the link off the page) and
    the message members copy to send friends."""

    website_url = forms.URLField(
        required=False,
        assume_scheme="https",
        validators=_WEB_LINK,
        label="Website event page link",
        widget=forms.URLInput(attrs={"placeholder": "https://pastlives.space/..."}),
    )
    eventbrite_url = forms.URLField(
        required=False,
        assume_scheme="https",
        validators=_WEB_LINK,
        label="Eventbrite link",
        widget=forms.URLInput(attrs={"placeholder": "https://www.eventbrite.com/e/..."}),
    )

    class Meta:
        model = HubFlyer
        fields = ["website_url", "eventbrite_url", "share_message"]
        labels = {"share_message": "Message to send friends"}
        help_texts = {"share_message": "The share link is added after it."}
        widgets = {"share_message": forms.Textarea(attrs={"rows": 4})}
