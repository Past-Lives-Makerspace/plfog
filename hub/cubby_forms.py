"""Forms for guild cubbies (#977): the guild page switch, a shelf unit, a cubby and the guild's Pricing.

Money is typed in dollars and stored in cents. The price rules are the models' own
(``membership.models.cubby_price_problems``), and where a cubby may sit is
``Cubby.placement_problems``, so the form only puts each refusal on its field.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

from django import forms
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString

from membership.models import Cubby, Guild, ShelfUnit, cubby_price_problems


class MoneyInput(forms.TextInput):
    """A dollar box drawn with its "$" inside (``.pl-cubby-money``), 6rem wide, decimal keypad."""

    def __init__(self, attrs: dict[str, Any] | None = None) -> None:
        super().__init__(attrs={"inputmode": "decimal", "autocomplete": "off", **(attrs or {})})

    def render(self, name: str, value: Any, attrs: dict[str, Any] | None = None, renderer: Any = None) -> SafeString:
        box = super().render(name, value, attrs, renderer)
        return format_html(
            '<span class="pl-cubby-money"><span class="pl-cubby-money__sign" aria-hidden="true">$</span>{}</span>', box
        )


#: Which of the four little squares light up in each shape's chip, top left first.
_SHAPE_PICS: dict[str, tuple[bool, bool, bool, bool]] = {
    Cubby.Shape.ONE: (True, False, False, False),
    Cubby.Shape.WIDE: (True, True, False, False),
    Cubby.Shape.TALL: (True, False, True, False),
    Cubby.Shape.BIG: (True, True, True, True),
}


class ShapeChips(forms.RadioSelect):
    """The Takes Up chips: four radio chips, each with a two by two picture of the squares it takes."""

    def render(self, name: str, value: Any, attrs: dict[str, Any] | None = None, renderer: Any = None) -> SafeString:
        base_id = (attrs or {}).get("id", f"id_{name}")
        chips = []
        for index, (option_value, label) in enumerate(self.choices):
            pic = format_html_join(
                "", "<i{}></i>", ((SafeString(' class="on"') if on else "",) for on in _SHAPE_PICS[str(option_value)])
            )
            checked = " checked" if str(value) == str(option_value) else ""
            chips.append(
                format_html(
                    '<label class="pl-cubby-span"><input type="radio" name="{}" value="{}" id="{}_{}"{}>'
                    '<span class="pl-cubby-span__pic" aria-hidden="true">{}</span>{}</label>',
                    name,
                    option_value,
                    base_id,
                    index,
                    SafeString(checked),
                    pic,
                    label,
                )
            )
        return format_html(
            '<div class="pl-cubby-spans" role="radiogroup" aria-label="Takes Up">{}</div>', SafeString("".join(chips))
        )


def _dollars(cents: int | None) -> Decimal | None:
    """Stored cents as the figure a dollar box starts with: 1500 is 15, 1550 is 15.50."""
    if cents is None:
        return None
    amount = Decimal(cents) / 100
    return amount.quantize(Decimal("1")) if cents % 100 == 0 else amount.quantize(Decimal("0.01"))


def _cents(amount: Decimal) -> int:
    return int(amount * 100)


def _money_field(label: str, *, required: bool = True) -> forms.DecimalField:
    return forms.DecimalField(
        label=label,
        required=required,
        min_value=Decimal("0"),
        max_digits=6,
        decimal_places=2,
        widget=MoneyInput(),
        error_messages={
            "invalid": "Enter dollars, like 15 or 15.50.",
            "max_decimal_places": "Use whole cents.",
            "max_digits": "That is more than a cubby can cost.",
            "max_whole_digits": "That is more than a cubby can cost.",
            "min_value": "Enter an amount of $1 or more.",
            "required": "Enter an amount.",
        },
    )


class GuildCubbiesSettingsForm(forms.ModelForm):
    """The one switch that puts the Cubbies tab on the guild page, in Guild Settings and the manager."""

    class Meta:
        model = Guild
        fields = ["show_cubbies_tab"]
        labels = {"show_cubbies_tab": "Show a Cubbies Tab on the Guild Page"}


class ShelfUnitForm(forms.ModelForm):
    """A shelf unit's name and size: the Add Shelf Unit modal and each unit card's name and steppers."""

    class Meta:
        model = ShelfUnit
        fields = ["name", "rows", "columns"]
        labels = {"name": "Shelf Name", "rows": "Rows", "columns": "Across"}
        widgets = {
            "name": forms.TextInput(attrs={"maxlength": 60, "class": "pl-cubby-input--text"}),
            "rows": forms.Select(
                choices=[(n, n) for n in range(1, ShelfUnit.MAX_ROWS + 1)], attrs={"class": "pl-cubby-input--code"}
            ),
            "columns": forms.Select(
                choices=[(n, n) for n in range(1, ShelfUnit.MAX_COLUMNS + 1)], attrs={"class": "pl-cubby-input--code"}
            ),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for name in self.fields:
            self.fields[name].help_text = ""
        self.fields["name"].error_messages["required"] = "Give the shelf a name."


class CubbyForm(forms.ModelForm):
    """One cubby's page (#977): label, spot, size, shape, photo, note, its own price and Out of Service.

    Placement is checked here so each refusal lands on its field; the view saves through
    ``membership.cubbies.save_cubby``, which checks again under the guild lock.
    """

    shape = forms.ChoiceField(label="Takes Up", choices=Cubby.Shape.choices, widget=ShapeChips)
    set_own_price = forms.BooleanField(label="Set Its Own Price", required=False)
    floor = _money_field("Minimum", required=False)
    suggested = _money_field("Suggested", required=False)

    class Meta:
        model = Cubby
        fields = ["label", "row", "column", "size", "photo", "note", "out_of_service"]
        labels = {
            "label": "Label",
            "row": "Row",
            "column": "Column",
            "size": "Size",
            "photo": "Photo",
            "note": "Note for Members",
            "out_of_service": "Out of Service",
        }
        widgets = {
            "label": forms.TextInput(attrs={"class": "pl-cubby-input--code", "maxlength": 8}),
            "size": forms.TextInput(attrs={"class": "pl-cubby-input--text", "maxlength": 60}),
            "note": forms.Textarea(attrs={"rows": 3, "maxlength": 300}),
        }

    def __init__(self, *args: Any, unit: ShelfUnit, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.unit = unit
        self.instance.unit = unit
        for name in ("row", "column"):
            limit = unit.rows if name == "row" else unit.columns
            self.fields[name] = forms.TypedChoiceField(
                label=self.fields[name].label,
                choices=[(n, n) for n in range(1, limit + 1)],
                coerce=int,
                widget=forms.Select(attrs={"class": "pl-cubby-input--code"}),
            )
        for name in self.fields:
            self.fields[name].help_text = ""
        self.fields["size"].help_text = "Width x height x depth, the way members would measure it."
        self.fields["out_of_service"].help_text = "Members see it hatched and can't rent it."
        self.fields["label"].error_messages["required"] = "Give the cubby a label, like A1."
        guild = unit.guild
        self.fields["set_own_price"].help_text = (
            f"When off, it uses the guild price: ${_dollars(guild.cubby_floor_cents)} minimum, "
            f"${_dollars(guild.cubby_suggested_cents)} suggested."
        )
        self.fields["set_own_price"].widget.attrs["x-model"] = "ownPrice"
        self.fields["shape"].initial = self.instance.shape if self.instance.pk else Cubby.Shape.ONE
        self.fields["set_own_price"].initial = self.instance.pk is not None and not self.instance.uses_guild_price
        self.fields["floor"].initial = _dollars(self.instance.floor_cents)
        self.fields["suggested"].initial = _dollars(self.instance.suggested_cents)

    @property
    def own_price_on(self) -> bool:
        """Whether the Minimum and Suggested boxes start open: as posted on a failed save, else as stored."""
        if self.is_bound:
            return bool(self.data.get(self.add_prefix("set_own_price")))
        return bool(self.fields["set_own_price"].initial)

    def clean(self) -> dict[str, Any]:
        cleaned = cast(dict[str, Any], super().clean())
        if not cleaned.get("set_own_price"):
            # The boxes are hidden while the switch is off; whatever they hold is not a price.
            for name in ("floor", "suggested"):
                self.errors.pop(name, None)
                cleaned.pop(name, None)
        else:
            floor, suggested = cleaned.get("floor"), cleaned.get("suggested")
            if floor is None and "floor" not in self.errors:
                self.add_error("floor", "Enter the minimum, or turn off Set Its Own Price.")
            if suggested is None and "suggested" not in self.errors:
                self.add_error("suggested", "Enter the suggested amount, or turn off Set Its Own Price.")
            if floor is not None and suggested is not None:
                for name, message in cubby_price_problems(_cents(floor), _cents(suggested)).items():
                    self.add_error(name, message)
        shape = cleaned.get("shape")
        if shape and not self.errors:
            cubby = self.instance
            cubby.label = cleaned["label"]
            cubby.row = cleaned["row"]
            cubby.column = cleaned["column"]
            cubby.width, cubby.height = Cubby.SHAPE_CELLS[shape]
            for name, message in cubby.placement_problems().items():
                self.add_error(name, message)
        return cleaned

    def save(self, commit: bool = True) -> Cubby:
        """Fill in the shape and the price; the view saves through ``save_cubby``."""
        cubby = super().save(commit=False)
        cubby.width, cubby.height = Cubby.SHAPE_CELLS[self.cleaned_data["shape"]]
        if self.cleaned_data["set_own_price"]:
            cubby.floor_cents = _cents(self.cleaned_data["floor"])
            cubby.suggested_cents = _cents(self.cleaned_data["suggested"])
        else:
            cubby.floor_cents = None
            cubby.suggested_cents = None
        if commit:
            cubby.save()
        return cubby


def _leader_titles(guild: Guild) -> dict[int, str]:
    """Each lead or staff member's title on ``guild`` for the payee select: "Guild Lead", "Treasurer"."""
    titles: dict[int, list[str]] = {}
    if guild.guild_lead_id is not None:
        titles[guild.guild_lead_id] = ["Guild Lead"]
    for staff in guild.staff_memberships.all():
        titles.setdefault(staff.member_id, []).append(staff.display_title)
    return {pk: " and ".join(names) for pk, names in titles.items()}


class _PayeeChoiceField(forms.ModelChoiceField):
    """Who gets paid: each person as "Name (Title)", the way the Pricing mockup lists them."""

    def __init__(self, *args: Any, titles: dict[int, str], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.titles = titles

    def label_from_instance(self, obj: Any) -> str:
        return f"{obj.display_name} ({self.titles[obj.pk]})"


class CubbyPricingForm(forms.ModelForm):
    """The guild's Pricing tab: its monthly minimum and suggestion, and Who gets paid."""

    floor = _money_field("Minimum a Month")
    suggested = _money_field("Suggested a Month")

    #: The refusal for a Who gets paid pick outside the guild's lead and staff.
    PAYEE_NOT_STAFF = "Pick the guild lead or someone on the guild's staff."

    class Meta:
        model = Guild
        fields = ["cubby_payee"]
        labels = {"cubby_payee": "Guild Payee"}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        guild = cast(Guild, self.instance)
        field = _PayeeChoiceField(queryset=guild.cubby_payee_choices(), titles=_leader_titles(guild))
        field.label = "Guild Payee"
        self.fields["cubby_payee"] = field
        field.required = False
        field.empty_label = "No one yet"
        field.help_text = ""
        field.error_messages["invalid_choice"] = self.PAYEE_NOT_STAFF
        field.widget.attrs["x-model"] = "payee"
        self.fields["floor"].initial = _dollars(guild.cubby_floor_cents)
        self.fields["suggested"].initial = _dollars(guild.cubby_suggested_cents)
        self.fields["floor"].widget.attrs["x-model"] = "floor"
        self.fields["suggested"].widget.attrs["x-model"] = "suggested"

    def clean(self) -> dict[str, Any]:
        cleaned = cast(dict[str, Any], super().clean())
        floor, suggested = cleaned.get("floor"), cleaned.get("suggested")
        if floor is not None and suggested is not None:
            for name, message in cubby_price_problems(_cents(floor), _cents(suggested)).items():
                self.add_error(name, message)
        return cleaned

    def save(self, commit: bool = True) -> Guild:
        guild = cast(Guild, super().save(commit=False))
        guild.cubby_floor_cents = _cents(self.cleaned_data["floor"])
        guild.cubby_suggested_cents = _cents(self.cleaned_data["suggested"])
        if commit:
            guild.save(update_fields=["cubby_floor_cents", "cubby_suggested_cents", "cubby_payee"])
        return guild
