"""The monthly amount a member picks when renting a cubby or changing what they pay (#978).

Radio chips (Minimum, Suggested, Extra Help, and Other on the rent sheet or Current on the
change sheet), from ``mockups/guild-cubbies.html`` views rent and mine-change. The form only
turns the pick into cents and puts the floor rule's refusal on the field the member sees;
:func:`membership.cubby_rentals.amount_problem` is the rule, and the service checks it again
under its lock.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from django import forms

from hub.cubby_forms import MoneyInput, _cents, _dollars
from membership.cubby_rentals import amount_problem
from membership.models import CUBBY_PRICE_MAX_CENTS, Cubby, cubby_dollars

#: What Extra Help adds to the suggested amount.
EXTRA_HELP_CENTS = 500


@dataclass(frozen=True)
class AmountChip:
    """One chip: its radio value, the price it shows (or "Other") and the word under it."""

    key: str
    cents: int | None
    sub: str

    @property
    def value_display(self) -> str:
        """The big line on the chip: "$15", or "Other"."""
        return cubby_dollars(self.cents) if self.cents is not None else "Other"


def amount_chips(cubby: Cubby, *, current_cents: int | None = None) -> list[AmountChip]:
    """The chips for ``cubby``: Minimum, Suggested and Extra Help, then Current (changing) or Other (renting).

    A chip whose price another chip already shows is left out, so no two chips say the same.
    """
    candidates = [
        AmountChip("floor", cubby.floor, "Minimum"),
        AmountChip("suggested", cubby.suggested, "Suggested"),
        AmountChip("extra", min(cubby.suggested + EXTRA_HELP_CENTS, CUBBY_PRICE_MAX_CENTS), "Extra Help"),
    ]
    if current_cents is not None:
        candidates.append(AmountChip("current", current_cents, "Current"))
    chips: list[AmountChip] = []
    seen: set[int] = set()
    for chip in candidates:
        assert chip.cents is not None
        if chip.cents in seen:
            continue
        seen.add(chip.cents)
        chips.append(chip)
    if current_cents is None:
        chips.append(AmountChip("other", None, "You Choose"))
    return chips


class CubbyAmountForm(forms.Form):
    """Pick a monthly amount for ``cubby``. ``current_cents`` makes it the Change Amount form.

    ``cleaned_data["amount_cents"]`` is the pick in cents. Renting opens on Suggested; changing
    opens on the chip matching what the member pays now.
    """

    choice = forms.ChoiceField(widget=forms.RadioSelect, error_messages={"required": "Pick an amount."})
    other = forms.DecimalField(
        label="Your amount",
        required=False,
        min_value=0,
        max_digits=6,
        decimal_places=2,
        widget=MoneyInput(attrs={"class": "pl-cubby-other__input"}),
        error_messages={
            "invalid": "Enter dollars, like 15 or 15.50.",
            "max_decimal_places": "Use whole cents.",
            "max_digits": "That is more than a cubby can cost.",
            "max_whole_digits": "That is more than a cubby can cost.",
            "min_value": "Enter an amount of $1 or more.",
        },
    )

    def __init__(self, *args: Any, cubby: Cubby, current_cents: int | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.cubby = cubby
        self.current_cents = current_cents
        self.chips = amount_chips(cubby, current_cents=current_cents)
        cast(forms.ChoiceField, self.fields["choice"]).choices = [(chip.key, chip.value_display) for chip in self.chips]
        # The sheet's Alpine state (hub/cubbies/_amount_state.html) reads and checks the Other box live.
        self.fields["other"].widget.attrs.update(
            {"x-model": "other", "@input": "touched = true", ":aria-invalid": "error ? 'true' : 'false'"}
        )
        if not self.is_bound:
            start = "suggested" if current_cents is None else self._chip_for(current_cents)
            self.initial.setdefault("choice", start if start in self._keys else self.chips[0].key)
            self.initial.setdefault("other", _dollars(cubby.suggested))

    @property
    def _keys(self) -> set[str]:
        return {chip.key for chip in self.chips}

    def _chip_for(self, cents: int) -> str:
        return next((chip.key for chip in self.chips if chip.cents == cents), "")

    @property
    def picked(self) -> str:
        """The chip the sheet shows ticked: the posted one, else the starting one."""
        return str(self.data.get("choice", "")) if self.is_bound else str(self.initial["choice"])

    def clean(self) -> dict[str, Any]:
        super().clean()
        cleaned = self.cleaned_data
        key = cleaned.get("choice")
        if key is None:
            return cleaned
        cents: int | None
        if key == "other":
            other = cleaned.get("other")
            if other is None:
                if "other" not in self.errors:
                    self.add_error("other", "Enter your amount.")
                return cleaned
            cents = _cents(other)
        else:
            cents = next(chip.cents for chip in self.chips if chip.key == key)
        assert cents is not None
        if cents != self.current_cents:
            problem = amount_problem(self.cubby, cents)
            if problem:
                self.add_error("other" if key == "other" else "choice", problem)
                return cleaned
        cleaned["amount_cents"] = cents
        return cleaned

    @property
    def error_text(self) -> str:
        """Every refusal as one sentence for the sheet's single error line."""
        return " ".join(str(message) for messages in self.errors.values() for message in messages)
