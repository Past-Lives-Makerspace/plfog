"""Forms for billing admin operations and tab-item entry."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory

from billing.models import BillingSettings, Product, ProductRevenueSplit
from membership.models import Guild, Member

if TYPE_CHECKING:
    from django.contrib.auth.base_user import AbstractBaseUser
    from django.contrib.auth.models import AnonymousUser

    from billing.exceptions import TabLimitExceededError, TabLockedError  # noqa: F401
    from billing.models import Tab, TabEntry

    UserLike = AbstractBaseUser | AnonymousUser | None


# Context values used by TabItemForm to select field set + editability.
CONTEXT_MEMBER_GUILD_PAGE = "member_guild_page"
CONTEXT_MEMBER_TAB_PAGE = "member_tab_page"
CONTEXT_ADMIN_DASHBOARD = "admin_dashboard"

VALID_CONTEXTS = {
    CONTEXT_MEMBER_GUILD_PAGE,
    CONTEXT_MEMBER_TAB_PAGE,
    CONTEXT_ADMIN_DASHBOARD,
}


class TabItemForm(forms.Form):
    """Unified tab-item entry form for admin and member contexts.

    Three contexts:
      * ``member_guild_page`` — guild is fixed; member picks a product (or
        enters custom description + amount for EYOP).  Splits for custom
        EYOP entries default to ``admin %`` from ``BillingSettings`` plus
        the rest to the page's guild.
      * ``member_tab_page`` — member adds an item from their tab page; must
        pick a product (splits come from the product).
      * ``admin_dashboard`` — staff adds an entry to a member's tab.  Either
        pick a product (splits come from the product) or post a
        ``CustomSplitFormSet`` alongside.
    """

    description = forms.CharField(
        max_length=500,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "What is this charge for?"}),
        label="Description",
    )
    amount = forms.DecimalField(
        max_digits=8,
        decimal_places=2,
        required=False,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(attrs={"placeholder": "0.00", "step": "0.01"}),
        label="Amount ($)",
    )

    def __init__(
        self,
        *args: Any,
        context: str,
        user: "UserLike" = None,
        guild: Guild | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        if context not in VALID_CONTEXTS:
            raise ValueError(f"Unknown TabItemForm context: {context}")
        self.context = context
        self.user = user
        self.fixed_guild = guild

        # Context-dependent fields
        if context == CONTEXT_ADMIN_DASHBOARD:
            self.fields["member"] = forms.ModelChoiceField(
                queryset=Member.objects.filter(status=Member.Status.ACTIVE),
                label="Member",
            )
            self.fields["product"] = forms.ModelChoiceField(
                queryset=Product.objects.select_related("guild"),
                required=False,
                empty_label="— Manual entry —",
                label="Product",
            )
            self.fields["guild"] = forms.ModelChoiceField(
                queryset=Guild.objects.filter(is_active=True).order_by("name"),
                required=False,
                empty_label="— Auto (from product) —",
                label="Guild",
            )
        elif context == CONTEXT_MEMBER_TAB_PAGE:
            self.fields["product"] = forms.ModelChoiceField(
                queryset=Product.objects.select_related("guild"),
                required=False,
                empty_label="— Manual entry (no product) —",
                label="Product",
            )
        elif (
            context == CONTEXT_MEMBER_GUILD_PAGE
        ):  # pragma: no branch — VALID_CONTEXTS guard makes other branch unreachable
            if guild is None:
                raise ValueError("member_guild_page context requires guild=<Guild>")
            self.fields["description"].required = True
            self.fields["amount"].required = True
            self.fields["quantity"] = forms.IntegerField(
                min_value=1,
                max_value=99,
                initial=1,
                required=True,
                widget=forms.NumberInput(attrs={"step": "1", "value": "1"}),
                label="Quantity",
            )

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        product = cleaned.get("product")
        self._fill_from_product(cleaned, product)
        return cleaned

    def _fill_from_product(self, cleaned: dict[str, Any], product: Product | None) -> None:
        if product is not None:
            cleaned["description"] = product.name
            cleaned["amount"] = product.price
            return
        if self.context == CONTEXT_MEMBER_GUILD_PAGE:
            return
        if not cleaned.get("description") or not cleaned.get("amount"):
            raise forms.ValidationError("Either select a product or enter a description and amount.")

    def save(
        self,
        *,
        tab: Tab,
        splits: list[dict[str, Any]] | None = None,
    ) -> TabEntry:
        """Create a TabEntry on ``tab`` using ``Tab.add_entry``.

        For product entries, splits come from the product (pass
        ``splits=None``). For custom entries, ``splits`` is required.
        """
        if not self.is_valid():
            raise RuntimeError("call form.is_valid() before save()")
        product = self.cleaned_data.get("product")
        if product is None and splits is None:
            raise ValueError("Custom entries (no product) require explicit splits.")
        return tab.add_entry(
            description=self.cleaned_data["description"],
            amount=self.cleaned_data["amount"],
            added_by=self.user,  # type: ignore[arg-type]  # views gate on @login_required
            is_self_service=(self.context != CONTEXT_ADMIN_DASHBOARD),
            product=product,
            splits=splits,
        )

    def apply_to_tab(
        self,
        tab: Tab,
        *,
        added_by: "UserLike",
        is_self_service: bool,
    ) -> TabEntry:
        """Back-compat wrapper used by the hub views.

        For ``member_guild_page`` custom entries, auto-constructs splits
        using ``BillingSettings.default_admin_percent`` on admin plus the
        remainder to the page's fixed guild.  Admin-context callers should
        prefer ``save()`` directly so they can attach a custom splits
        formset.
        """
        if not self.is_valid():
            raise RuntimeError("call form.is_valid() before apply_to_tab()")
        # Re-bind context-ish attrs the plan's save() uses
        self.user = added_by
        product = self.cleaned_data.get("product")
        splits: list[dict[str, Any]] | None = None
        if product is None:
            splits = self._default_custom_splits()
        return tab.add_entry(
            description=self.cleaned_data["description"],
            amount=self.cleaned_data["amount"],
            added_by=added_by,  # type: ignore[arg-type]  # views gate on @login_required
            is_self_service=is_self_service,
            product=product,
            splits=splits,
        )

    def _default_custom_splits(self) -> list[dict[str, Any]]:
        """Default splits for a custom entry when no explicit formset is posted.

        Only sensible for ``member_guild_page`` where a fixed guild is known.
        The admin dashboard path must always post an explicit splits formset.
        """
        if self.context != CONTEXT_MEMBER_GUILD_PAGE or self.fixed_guild is None:
            raise ValueError("Custom entries outside member_guild_page require explicit splits.")
        admin_percent: Decimal = BillingSettings.load().default_admin_percent
        guild_percent = Decimal("100") - admin_percent
        return [
            {
                "recipient_type": ProductRevenueSplit.RecipientType.ADMIN,
                "guild": None,
                "percent": admin_percent,
            },
            {
                "recipient_type": ProductRevenueSplit.RecipientType.GUILD,
                "guild": self.fixed_guild,
                "percent": guild_percent,
            },
        ]


class _SplitRowForm(forms.Form):
    """Single row of a non-model ``CustomSplitFormSet``."""

    recipient_type = forms.ChoiceField(choices=ProductRevenueSplit.RecipientType.choices)
    guild = forms.ModelChoiceField(queryset=Guild.objects.all(), required=False)
    percent = forms.DecimalField(
        max_digits=5,
        decimal_places=2,
        min_value=Decimal("0.01"),
        max_value=Decimal("100"),
    )


class _BaseCustomSplitFormSet(forms.BaseFormSet):
    """Validates sum=100, >=1 row, no duplicate guilds, recipient/guild rules.

    Intentionally parallel to ``_BaseProductSplitFormSet`` — same shape, but
    operates on a non-model ``BaseFormSet`` since custom-entry splits aren't
    backed by ``ProductRevenueSplit`` rows.
    """

    def clean(self) -> None:
        super().clean()
        if any(self.errors):
            return
        active = [f.cleaned_data for f in self.forms if f.cleaned_data and not f.cleaned_data.get("DELETE", False)]
        if not active:  # pragma: no cover — defensive; validate_min=True raises before clean() runs
            raise forms.ValidationError("At least one split row is required.")
        total = sum((row["percent"] for row in active), Decimal("0"))
        if total != Decimal("100"):
            raise forms.ValidationError(f"Splits must sum to 100% — currently {total}%.")
        seen_admin = False
        seen_guilds: set[int] = set()
        for row in active:
            rtype = row["recipient_type"]
            guild = row.get("guild")
            if rtype == ProductRevenueSplit.RecipientType.ADMIN:
                if guild is not None:
                    raise forms.ValidationError("Admin rows must not select a guild.")
                if seen_admin:
                    raise forms.ValidationError("Only one Admin row is allowed.")
                seen_admin = True
            else:
                if guild is None:
                    raise forms.ValidationError("Guild rows must select a guild.")
                if guild.pk in seen_guilds:
                    raise forms.ValidationError(f"Guild '{guild.name}' appears more than once.")
                seen_guilds.add(guild.pk)

    def to_split_dicts(self) -> list[dict[str, Any]]:
        """Serialize active rows into the dict shape ``Tab.add_entry`` expects."""
        out: list[dict[str, Any]] = []
        for f in self.forms:
            if not f.cleaned_data or f.cleaned_data.get("DELETE"):
                continue
            out.append(
                {
                    "recipient_type": f.cleaned_data["recipient_type"],
                    "guild": f.cleaned_data.get("guild"),
                    "percent": f.cleaned_data["percent"],
                }
            )
        return out


CustomSplitFormSet = forms.formset_factory(
    _SplitRowForm,
    formset=_BaseCustomSplitFormSet,
    extra=0,
    min_num=1,
    validate_min=True,
    can_delete=True,
)


class VoidTabEntryForm(forms.Form):
    """Form for voiding a tab entry. Reason is required."""

    reason = forms.CharField(
        max_length=500,
        widget=forms.TextInput(attrs={"placeholder": "Reason for voiding"}),
        label="Void Reason",
    )


class BillingSettingsForm(forms.ModelForm):
    """Admin form for editing the BillingSettings singleton."""

    class Meta:
        model = BillingSettings
        fields = [
            "charge_frequency",
            "charge_time",
            "charge_day_of_week",
            "charge_day_of_month",
            "default_tab_limit",
            "default_admin_percent",
            "max_retry_attempts",
            "retry_interval_hours",
        ]
        widgets = {
            "charge_frequency": forms.Select(),
            "charge_time": forms.TimeInput(attrs={"type": "time"}),
        }

    def clean_default_tab_limit(self) -> Decimal:
        value: Decimal = self.cleaned_data["default_tab_limit"]
        if value < Decimal("0.00"):
            raise forms.ValidationError("Tab limit must be zero or positive.")
        return value


def _format_percent(total: Decimal) -> str:
    """Render a percent sum without scientific notation or trailing zeros (105.00 -> "105")."""
    return f"{total:.2f}".rstrip("0").rstrip(".")


class ReconciliationSettingsForm(forms.ModelForm):
    """Editable split percentages on BillingSettings — each triad must sum to 100.00."""

    _ORIENTATION = ["orientation_orientator_percent", "orientation_guild_percent", "orientation_pl_percent"]
    _CLASS = ["class_instructor_percent", "class_guild_percent", "class_pl_percent"]

    class Meta:
        model = BillingSettings
        fields = [
            "orientation_orientator_percent",
            "orientation_guild_percent",
            "orientation_pl_percent",
            "class_instructor_percent",
            "class_guild_percent",
            "class_pl_percent",
        ]

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        self._check_triad(cleaned, self._ORIENTATION, "Orientation")
        self._check_triad(cleaned, self._CLASS, "Class")
        return cleaned

    def _check_triad(self, cleaned: dict[str, Any], fields: list[str], label: str) -> None:
        raw = [cleaned.get(name) for name in fields]
        if any(value is None for value in raw):
            return  # a per-field bound error already fired
        values = [Decimal(str(value)) for value in raw]
        total = sum(values, Decimal("0"))
        if total != Decimal("100"):
            message = f"{label} percentages must add up to 100 (currently {_format_percent(total)})."
            for name in fields:
                self.add_error(name, message)


class TransactionAdjustmentForm(forms.Form):
    """Per-transaction reconciliation override: omit the transaction or re-split it.

    Class / orientation transactions expose one percent field per recipient (the
    triad must sum to 100 unless omitted). Tab charges are omit-only.
    """

    _PERCENT_KEYS: dict[str, list[tuple[str, str]]] = {
        "class": [("instructor", "Instructor"), ("guild", "Guild"), ("pl", "Past Lives")],
        "orientation": [("orientator", "Orientator"), ("guild", "Guild"), ("pl", "Past Lives")],
        "tab": [],
    }

    is_omitted = forms.BooleanField(required=False, label="Do not count this transaction")
    reason = forms.CharField(required=False, widget=forms.TextInput, label="Reason")

    def __init__(self, *args: Any, source_kind: str, source_pk: int, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if source_kind not in self._PERCENT_KEYS:
            raise ValueError(f"Unknown source_kind: {source_kind}")
        self.source_kind = source_kind
        self.source_pk = source_pk
        self.percent_keys = [key for key, _ in self._PERCENT_KEYS[source_kind]]
        for key, label in self._PERCENT_KEYS[source_kind]:
            self.fields[f"percent_{key}"] = forms.DecimalField(
                max_digits=5,
                decimal_places=2,
                min_value=Decimal("0"),
                max_value=Decimal("100"),
                required=False,
                label=f"{label} %",
            )
        self.fields["reason"].help_text = "Internal note. Members never see this."

    @property
    def percent_fields(self) -> list[Any]:
        """The bound percent fields for this source kind (empty for tab charges)."""
        return [self[f"percent_{key}"] for key in self.percent_keys]

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        if cleaned.get("is_omitted") or not self.percent_keys:
            return cleaned
        raw = [cleaned.get(f"percent_{key}") for key in self.percent_keys]
        if any(value is None for value in raw):
            raise forms.ValidationError("Enter all three percentages.")
        if sum((Decimal(str(value)) for value in raw), Decimal("0")) != Decimal("100"):
            raise forms.ValidationError("The three percentages must add up to 100.")
        return cleaned

    def save(self, *, actor: Any) -> Any:
        from billing.models import TransactionAdjustment

        is_omitted = bool(self.cleaned_data["is_omitted"])
        override: dict[str, str] | None = None
        if not is_omitted and self.percent_keys:
            override = {key: str(self.cleaned_data[f"percent_{key}"]) for key in self.percent_keys}
        adjustment, _created = TransactionAdjustment.objects.update_or_create(
            source_kind=self.source_kind,
            source_pk=self.source_pk,
            defaults={
                "is_omitted": is_omitted,
                "override_percents": override,
                "reason": self.cleaned_data.get("reason", ""),
                "created_by": actor if getattr(actor, "pk", None) else None,
            },
        )
        return adjustment


class ConnectPlatformSettingsForm(forms.ModelForm):
    """Admin form for editing the platform Stripe credentials on BillingSettings.

    Lives separately from BillingSettingsForm so it can be POSTed independently
    from a dedicated card on the Settings tab.
    """

    class Meta:
        model = BillingSettings
        fields = [
            "connect_enabled",
            "test_mode",
            "connect_client_id",
            "connect_platform_publishable_key",
            "connect_platform_secret_key",
            "connect_platform_webhook_secret",
            "test_connect_client_id",
            "test_connect_platform_publishable_key",
            "test_connect_platform_secret_key",
            "test_connect_platform_webhook_secret",
        ]
        widgets = {
            "connect_client_id": forms.TextInput(attrs={"placeholder": "ca_…", "autocomplete": "off"}),
            "connect_platform_publishable_key": forms.TextInput(
                attrs={"placeholder": "pk_live_…", "autocomplete": "off"}
            ),
            "connect_platform_secret_key": forms.PasswordInput(
                render_value=True, attrs={"placeholder": "sk_live_…", "autocomplete": "off"}
            ),
            "connect_platform_webhook_secret": forms.PasswordInput(
                render_value=True, attrs={"placeholder": "whsec_…", "autocomplete": "off"}
            ),
            "test_connect_client_id": forms.TextInput(attrs={"placeholder": "ca_…", "autocomplete": "off"}),
            "test_connect_platform_publishable_key": forms.TextInput(
                attrs={"placeholder": "pk_test_…", "autocomplete": "off"}
            ),
            "test_connect_platform_secret_key": forms.PasswordInput(
                render_value=True, attrs={"placeholder": "sk_test_…", "autocomplete": "off"}
            ),
            "test_connect_platform_webhook_secret": forms.PasswordInput(
                render_value=True, attrs={"placeholder": "whsec_…", "autocomplete": "off"}
            ),
        }

    def clean(self) -> dict:
        """Require only the active mode's four credential fields when Connect is enabled.

        Mirrors ``BillingSettings.clean`` so the inactive slot may be blank or
        pre-filled — both key sets can be saved side by side.
        """
        cleaned = super().clean() or {}
        if cleaned.get("connect_enabled"):
            test_mode = cleaned.get("test_mode")
            for suffix in BillingSettings._CREDENTIAL_SUFFIXES:
                field_name = f"test_connect_{suffix}" if test_mode else f"connect_{suffix}"
                if not cleaned.get(field_name):
                    self.add_error(field_name, "Required when Stripe Connect is enabled in the current mode.")
        return cleaned


class ProductForm(forms.ModelForm):
    """Product fields only — splits are handled by ProductRevenueSplitFormSet."""

    class Meta:
        model = Product
        fields = ["name", "price", "guild"]


class _BaseProductSplitFormSet(BaseInlineFormSet):
    """Validates: >=1 active row, sum=100, no duplicates, recipient_type/guild rules."""

    def clean(self) -> None:
        super().clean()
        if any(self.errors):
            return  # let per-form errors surface first

        active_rows = [f.cleaned_data for f in self.forms if f.cleaned_data and not f.cleaned_data.get("DELETE", False)]
        if not active_rows:  # pragma: no cover — defensive; validate_min=True raises before clean() runs
            raise forms.ValidationError("At least one revenue split row is required.")

        self._check_total(active_rows)
        self._check_recipient_rules(active_rows)

    @staticmethod
    def _check_total(active_rows: list[dict[str, Any]]) -> None:
        total = sum((row["percent"] for row in active_rows), Decimal("0"))
        if total != Decimal("100"):
            raise forms.ValidationError(f"Revenue splits must sum to 100% — currently {total}%.")

    @staticmethod
    def _check_recipient_rules(active_rows: list[dict[str, Any]]) -> None:
        seen_admin = False
        seen_guilds: set[int] = set()
        for row in active_rows:
            rtype = row["recipient_type"]
            guild = row.get("guild")
            if rtype == ProductRevenueSplit.RecipientType.ADMIN:
                if guild is not None:  # pragma: no cover — model CheckConstraint catches this per-row before clean()
                    raise forms.ValidationError("Admin rows must not select a guild.")
                if seen_admin:
                    raise forms.ValidationError("Only one Admin row is allowed per product.")
                seen_admin = True
            elif rtype == ProductRevenueSplit.RecipientType.GUILD:
                if guild is None:  # pragma: no cover — model CheckConstraint catches this per-row before clean()
                    raise forms.ValidationError("Guild rows must select a guild.")
                if guild.pk in seen_guilds:
                    raise forms.ValidationError(
                        f"Guild '{guild.name}' appears more than once. Each guild may only appear in one split row."
                    )
                seen_guilds.add(guild.pk)


ProductRevenueSplitFormSet: Any = inlineformset_factory(
    Product,
    ProductRevenueSplit,
    formset=_BaseProductSplitFormSet,
    fields=["recipient_type", "guild", "percent"],
    extra=0,
    can_delete=True,
    min_num=1,
    validate_min=True,
)


def build_product_split_formset(
    *,
    data: Any = None,
    instance: Product | None = None,
    prefix: str = "splits",
) -> BaseInlineFormSet:
    """Convenience constructor used by views and tests so the prefix is consistent."""
    return ProductRevenueSplitFormSet(data=data, instance=instance, prefix=prefix)


class OrientationRefundForm(forms.Form):
    """Validates the orientation refund modal — amount bounds live here, not in the view.

    Mirrors ``classes.forms.PaymentRefundForm`` against the booking's refundable
    remainder: ``amount`` pre-fills with the full remainder (full refund is the
    default), ``reason`` is an internal note the payer never sees.
    """

    amount = forms.DecimalField(max_digits=8, decimal_places=2)
    reason = forms.CharField(required=False, widget=forms.TextInput)

    def __init__(self, *args: Any, booking: Any, **kwargs: Any) -> None:
        self.booking = booking
        refundable = (Decimal(booking.refundable_cents) / 100).quantize(Decimal("0.01"))
        kwargs.setdefault("initial", {})
        kwargs["initial"].setdefault("amount", refundable)
        super().__init__(*args, **kwargs)
        self.fields["amount"].label = "Amount"
        self.fields["amount"].help_text = f"Up to ${refundable:.2f}. Edit for a partial refund."
        self.fields["reason"].label = "Reason"
        self.fields["reason"].help_text = "Internal note. The payer never sees this."

    def clean_amount(self) -> Decimal:
        amount: Decimal = self.cleaned_data["amount"]
        refundable = Decimal(self.booking.refundable_cents) / 100
        if not Decimal("0.01") <= amount <= refundable:
            raise forms.ValidationError(f"Enter an amount between $0.01 and ${refundable:.2f}.")
        return amount

    @property
    def amount_cents(self) -> int:
        """The validated refund amount in cents — what ``issue_refund`` takes."""
        return int(self.cleaned_data["amount"] * 100)
