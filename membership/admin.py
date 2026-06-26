from __future__ import annotations

from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils import timezone
from django.utils.safestring import mark_safe
from unfold.admin import ModelAdmin, TabularInline

from .forms import MemberAdminForm
from .models import FundingSnapshot, Member, MemberEmail, MemberSkill, Skill, SkillCategory, VotePreference


# ---------------------------------------------------------------------------
# MemberEmail Inline
# ---------------------------------------------------------------------------


class MemberEmailInline(TabularInline):
    """Inline for the MemberEmail staging table.

    MemberEmail is a pre-signup staging table for members imported from
    Airtable who do not yet have a linked User account. Once a Member is
    linked to a User, ``allauth.EmailAddress`` becomes the source of truth
    for that member's emails, and the MemberEmail rows are dead data.

    This inline is therefore hidden entirely for members with a linked
    User — see ``MemberAdmin.get_inline_instances``. Showing it would
    invite admins to edit rows that no longer drive any behavior.

    See docs/superpowers/specs/2026-04-07-user-email-aliases-design.md.
    """

    model = MemberEmail
    extra = 1
    fields = ["email"]
    verbose_name = "Staged email (pre-signup)"
    verbose_name_plural = "Staged emails (pre-signup)"


# ---------------------------------------------------------------------------
# MemberAdmin
# ---------------------------------------------------------------------------


class ActiveStatusFilter(admin.SimpleListFilter):
    """Default filter that shows only active members unless another status is selected."""

    title = "status"
    parameter_name = "status"

    def lookups(self, request: HttpRequest, model_admin: ModelAdmin) -> list[tuple[str, str]]:  # type: ignore[override]
        return [
            ("all", "All"),
            *Member.Status.choices,
        ]

    def queryset(self, request: HttpRequest, queryset: QuerySet[Member]) -> QuerySet[Member]:
        if self.value() is None:
            return queryset.filter(status=Member.Status.ACTIVE)
        if self.value() == "all":
            return queryset
        return queryset.filter(status=self.value())


class HasUserFilter(admin.SimpleListFilter):
    """Filter to show only members with linked User accounts (i.e., they've logged in)."""

    title = "account type"
    parameter_name = "has_user"

    def lookups(self, request: HttpRequest, model_admin: ModelAdmin) -> list[tuple[str, str]]:  # type: ignore[override]
        return [
            ("yes", "Users — members who have logged into this web app"),
        ]

    def queryset(self, request: HttpRequest, queryset: QuerySet[Member]) -> QuerySet[Member]:
        if self.value() == "yes":
            return queryset.filter(user__isnull=False)
        return queryset


@admin.register(Member)
class MemberAdmin(ModelAdmin):
    change_list_template = "admin/membership/member/change_list.html"
    form = MemberAdminForm
    inlines = [MemberEmailInline]
    readonly_fields = ["email_aliases_link"]
    list_display = [
        "display_name",
        "_pre_signup_email",
        "status",
        "member_type",
        "fog_role",
        "join_date",
        "last_login_display",
    ]
    list_display_links = ["display_name"]
    list_filter = [ActiveStatusFilter, HasUserFilter, "member_type"]
    search_fields = ["full_legal_name", "preferred_name", "_pre_signup_email"]
    list_per_page = 100
    ordering = ["full_legal_name"]

    def get_inline_instances(self, request: HttpRequest, obj: Member | None = None) -> list:
        """Hide the MemberEmail staging inline once the member has a linked user.

        THREE-EMAIL-STORE NOTE: Once user_id is set, allauth.EmailAddress is the
        source of truth for emails. Showing the MemberEmail staging inline at that
        point would be confusing and let admins edit dead data.
        See docs/superpowers/specs/2026-04-07-user-email-aliases-design.md.
        """
        instances = super().get_inline_instances(request, obj)
        if obj is not None and obj.user_id is not None:
            instances = [i for i in instances if not isinstance(i, MemberEmailInline)]
        return instances

    def get_fieldsets(self, request: HttpRequest, obj: Member | None = None) -> list[tuple[str, dict]]:
        """Build fieldsets dynamically — fog_role only visible to superusers."""
        membership_fields: list[str] = [
            "membership_plan",
            "status",
            "member_type",
            "join_date",
            "cancellation_date",
            "committed_until",
        ]
        if request.user.is_superuser:
            membership_fields.insert(3, "fog_role")

        personal_fields: list[str] = [
            "full_legal_name",
            "preferred_name",
            "_pre_signup_email",
            "phone",
            "billing_name",
        ]

        # Show "user" link on edit, "create_user" checkbox on add
        if obj is not None:
            personal_fields.insert(0, "user")
            personal_fields.insert(1, "email_aliases_link")
        else:
            personal_fields.append("create_user")

        return [
            (
                "Personal Info",
                {
                    "fields": personal_fields,
                },
            ),
            (
                "Membership",
                {
                    "fields": membership_fields,
                },
            ),
            (
                "Emergency Contact",
                {
                    "fields": [
                        "emergency_contact_name",
                        "emergency_contact_phone",
                        "emergency_contact_relationship",
                    ],
                },
            ),
            (
                "Notes",
                {
                    "fields": ["notes"],
                },
            ),
        ]

    def save_model(self, request: HttpRequest, obj: Member, form: MemberAdminForm, change: bool) -> None:
        """Save the member, provisioning a linked User on add when appropriate.

        A new ACTIVE member is auto-provisioned by the ``auto_provision_member_user``
        signal (the "every member is a user" invariant). The "Create login immediately"
        checkbox forces provisioning for a member the signal would skip (e.g. one added
        in a non-ACTIVE status). ``provision_user_for_member`` is idempotent, so the two
        paths never double-create a User.
        """
        super().save_model(request, obj, form, change)

        if not change and form.cleaned_data["create_user"] and obj.user_id is None and obj._pre_signup_email:
            from membership.services.provisioning import provision_user_for_member

            provision_user_for_member(obj)

        if obj.user_id is not None:
            obj.sync_user_permissions()

    @admin.display(description="Email aliases")
    def email_aliases_link(self, obj: Member) -> str:
        """Render the Manage Aliases link for linked members only.

        THREE-EMAIL-STORE NOTE: This link appears only for members with a
        linked User. Unlinked members manage pre-signup emails via the
        MemberEmailInline below. See the aliases page spec at
        docs/superpowers/specs/2026-04-11-admin-email-aliases-design.md.
        """
        from django.urls import reverse
        from django.utils.html import format_html

        if obj.user_id is None:
            return mark_safe(  # noqa: S308
                '<span style="color: #888;">No linked user yet — use Staged Emails below.</span>'
            )
        url = reverse("admin_member_aliases", args=[obj.pk])
        return format_html('<a href="{}">Manage email aliases →</a>', url)

    def get_search_results(
        self, request: HttpRequest, queryset: QuerySet[Member], search_term: str
    ) -> tuple[QuerySet[Member], bool]:
        queryset, use_distinct = super().get_search_results(request, queryset, search_term)
        if search_term:
            alias_member_ids = MemberEmail.objects.filter(email__icontains=search_term).values_list(
                "member_id", flat=True
            )
            queryset = queryset | self.model.objects.filter(pk__in=alias_member_ids)
            use_distinct = True
        return queryset, use_distinct

    def get_queryset(self, request: HttpRequest) -> QuerySet[Member]:
        qs = super().get_queryset(request)
        return qs.select_related("membership_plan", "user")

    @admin.display(description="Name", ordering="full_legal_name")
    def display_name(self, obj: Member) -> str:
        return obj.display_name

    @admin.display(description="Last Login to FOG", ordering="user__last_login")
    def last_login_display(self, obj: Member) -> str:
        if obj.user is None or obj.user.last_login is None:
            return mark_safe('<span style="opacity:0.4">Never</span>')  # noqa: S308
        last = obj.user.last_login
        days_ago = (timezone.now() - last).days
        if days_ago == 0:
            return "Today"
        if days_ago == 1:
            return "Yesterday"
        return f"{days_ago} days ago"


# ---------------------------------------------------------------------------
# VotePreferenceAdmin
# ---------------------------------------------------------------------------


class PayingMemberFilter(admin.SimpleListFilter):
    """Filter vote preferences by whether the member is a paying (standard) member."""

    title = "paying status"
    parameter_name = "paying"

    def lookups(self, request: HttpRequest, model_admin: ModelAdmin) -> list[tuple[str, str]]:  # type: ignore[override]
        return [
            ("yes", "Paying members"),
            ("no", "Non-paying members"),
        ]

    def queryset(self, request: HttpRequest, queryset: QuerySet[VotePreference]) -> QuerySet[VotePreference]:
        if self.value() == "yes":
            return queryset.filter(member__member_type=Member.MemberType.STANDARD)
        if self.value() == "no":
            return queryset.exclude(member__member_type=Member.MemberType.STANDARD)
        return queryset


@admin.register(VotePreference)
class VotePreferenceAdmin(ModelAdmin):
    # Per-row counts were deliberately dropped from list_display: they required
    # a full FundingSnapshot.raw_votes scan per row, which scales as O(rows × snapshots)
    # on every changelist load. Per-member history lives on the change view instead.
    list_display = ["member", "guild_1st", "guild_2nd", "guild_3rd", "updated_at"]
    list_filter = [PayingMemberFilter]
    search_fields = ["member__full_legal_name", "member__preferred_name"]
    readonly_fields = ["voting_history"]

    def get_fieldsets(self, request: HttpRequest, obj: VotePreference | None = None) -> list[tuple[str, dict]]:
        fields: list[str] = ["member", "guild_1st", "guild_2nd", "guild_3rd", "updated_at"]
        sections: list[tuple[str, dict]] = [("Current Vote", {"fields": fields})]
        if obj is not None:
            sections.append(("Historical Votes", {"fields": ["voting_history"]}))
        return sections

    def get_readonly_fields(self, request: HttpRequest, obj: VotePreference | None = None) -> tuple[str, ...]:
        base = tuple(super().get_readonly_fields(request, obj))
        if obj is not None:
            return (*base, "updated_at")
        return base

    @admin.display(description="Past votes from funding snapshots")
    def voting_history(self, obj: VotePreference) -> str:
        """Render each snapshot where this member voted with their picks at that time.

        Reads ``FundingSnapshot.raw_votes`` (a denormalized JSON list of per-vote
        dicts captured at snapshot time) and filters to entries matching this
        member. Lets admins audit how their vote contributed to past point totals
        across cycles.
        """
        from django.utils.html import format_html_join

        rows = _member_snapshot_rows(obj.member_id)
        if not rows:
            return mark_safe(  # noqa: S308
                '<span style="color:#888">No snapshots recorded yet for this member.</span>'
            )
        header = (
            "<tr>"
            "<th style='text-align:left;padding:6px 10px'>Cycle</th>"
            "<th style='text-align:left;padding:6px 10px'>1st (5 pts)</th>"
            "<th style='text-align:left;padding:6px 10px'>2nd (3 pts)</th>"
            "<th style='text-align:left;padding:6px 10px'>3rd (2 pts)</th>"
            "<th style='text-align:left;padding:6px 10px'>Counted toward pool?</th>"
            "</tr>"
        )
        body = format_html_join(
            "",
            "<tr>"
            "<td style='padding:6px 10px'><a href='{}'>{}</a></td>"
            "<td style='padding:6px 10px'>{}</td>"
            "<td style='padding:6px 10px'>{}</td>"
            "<td style='padding:6px 10px'>{}</td>"
            "<td style='padding:6px 10px'>{}</td>"
            "</tr>",
            rows,
        )
        return mark_safe(  # noqa: S308
            f"<table style='border-collapse:collapse;width:100%'><thead>{header}</thead><tbody>{body}</tbody></table>"
        )


def _member_snapshot_rows(member_id: int) -> list[tuple]:
    """Build the per-snapshot row tuples for the voting_history readonly field."""
    from django.urls import reverse

    rows: list[tuple] = []
    snapshots = FundingSnapshot.objects.order_by("-snapshot_at").only("pk", "cycle_label", "raw_votes")
    for snap in snapshots:
        match = next(
            (v for v in snap.raw_votes or [] if v.get("member_id") == member_id),
            None,
        )
        if match is None:
            continue
        url = reverse("hub_admin_voting_history_detail", args=[snap.pk])
        rows.append(
            (
                url,
                snap.cycle_label,
                match.get("guild_1st_name", ""),
                match.get("guild_2nd_name", ""),
                match.get("guild_3rd_name", ""),
                "Yes" if match.get("is_paying") else "No (allocation only)",
            )
        )
    return rows


# ---------------------------------------------------------------------------
# FundingSnapshotAdmin
# ---------------------------------------------------------------------------


@admin.register(FundingSnapshot)
class FundingSnapshotAdmin(ModelAdmin):
    list_display = ["cycle_label", "snapshot_at", "contributor_count", "funding_pool", "analyzer_link"]
    readonly_fields = ["snapshot_at", "results", "raw_votes", "contributor_count", "funding_pool", "minimum_pool"]

    @admin.display(description="Analyzer")
    def analyzer_link(self, obj: FundingSnapshot) -> str:
        """Render a link to the admin snapshot analyzer for this row."""
        from django.urls import reverse
        from django.utils.html import format_html

        url = reverse("hub_admin_voting_history_detail", args=[obj.pk])
        return format_html('<a href="{}">Open analyzer →</a>', url)


# ---------------------------------------------------------------------------
# SkillAdmin — vocabulary curation + suggestion approval
# ---------------------------------------------------------------------------


@admin.register(Skill)
class SkillAdmin(ModelAdmin):
    list_display = ("name", "category", "status", "suggested_by")
    list_filter = ("status", "category")
    search_fields = ("name",)
    actions = ["approve_skills"]

    @admin.action(description="Approve selected skills")
    def approve_skills(self, request: HttpRequest, queryset: QuerySet[Skill]) -> None:
        queryset.update(status=Skill.Status.APPROVED)


@admin.register(SkillCategory)
class SkillCategoryAdmin(ModelAdmin):
    list_display = ("name", "sort_order")
    search_fields = ("name",)


@admin.register(MemberSkill)
class MemberSkillAdmin(ModelAdmin):
    list_display = ("member", "skill", "years_experience")
    list_filter = ("skill__category",)
    search_fields = ("member__full_legal_name", "skill__name")
    autocomplete_fields = ("member", "skill")


# ---------------------------------------------------------------------------
# Hide default User and EmailAddress admin pages
# ---------------------------------------------------------------------------

# Hide default User admin and allauth EmailAddress admin — members page is the
# single interface for user management.
from django.contrib.auth import get_user_model  # noqa: E402
from allauth.account.models import EmailAddress  # noqa: E402

admin.site.unregister(get_user_model())
admin.site.unregister(EmailAddress)
