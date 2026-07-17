"""BDD specs for DiscountCodeForm shape (auto-apply removed, uppercase code)."""

from __future__ import annotations

from classes.forms import DiscountCodeForm


def describe_DiscountCodeForm():
    def it_does_not_expose_the_auto_apply_field():
        assert "auto_apply" not in DiscountCodeForm().fields

    def it_force_uppercases_the_code_input():
        attrs = DiscountCodeForm().fields["code"].widget.attrs
        assert "text-transform:uppercase" in attrs["style"]
        assert attrs["oninput"] == "this.value = this.value.toUpperCase()"
