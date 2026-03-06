from __future__ import annotations

from django import forms

from .models import Buyable, Member


class BuyableForm(forms.ModelForm):
    class Meta:
        model = Buyable
        fields = ["name", "description", "image", "unit_price", "is_active"]


class MemberProfileForm(forms.ModelForm):
    class Meta:
        model = Member
        fields = [
            "preferred_name",
            "phone",
            "emergency_contact_name",
            "emergency_contact_phone",
            "emergency_contact_relationship",
        ]
