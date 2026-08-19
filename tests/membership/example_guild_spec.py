"""BDD specs for the Help Center's example guild seed (membership.example_guild)."""

from __future__ import annotations

from io import StringIO

from django.core.management import call_command

from membership.example_guild import EXAMPLE_GUILD_SLUG, seed_example_guild
from membership.models import Guild, GuildStaffMembership, Member, MembershipPlan


def describe_seed_example_guild():
    def it_creates_the_unlisted_guild_with_every_surface_filled(db):
        guild = seed_example_guild()

        assert guild.slug == EXAMPLE_GUILD_SLUG
        assert guild.name == "Cartographers Guild"
        assert guild.is_active is False
        assert guild.is_featured is False
        assert "fictional example guild" in guild.about
        assert guild.wishlist
        assert guild.essential_rules
        assert guild.faq_label == "Field Notes"
        assert guild.show_members is True
        assert guild.meeting_cadence == Guild.MeetingCadence.MONTHLY
        assert guild.banner_image
        assert guild.gallery_images.count() == 3
        assert guild.faq_items.count() == 3
        assert guild.links.count() == 3
        assert guild.announcements.published().count() == 3
        assert guild.meeting_notes.count() == 2
        assert guild.meeting_notes.get(title="August General Meeting").attachments.count() == 1

    def it_seeds_orientation_settings_enabled_but_closed(db):
        guild = seed_example_guild()
        settings = guild.orientation_settings
        assert settings.is_enabled is True
        assert settings.is_closed is True
        assert "example guild" in settings.closed_message
        # Both automatic emails are authored but switched off — nothing can send.
        assert settings.thankyou_email_enabled is False
        assert settings.join_email_enabled is False

    def it_creates_inert_fictional_members_only(db):
        guild = seed_example_guild()

        lead = guild.guild_lead
        assert lead is not None
        assert lead.full_legal_name == "Ada Meridian"
        staffed = {sm.member.full_legal_name: sm.display_title for sm in guild.staff_memberships.all()}
        assert staffed == {
            "Niko Contour": "Co-Lead",
            "June Azimuth": "Secretary",
            "Otto Scale": "Treasurer",
            "Rhea Compass": "Orientator",
            "Felix Atlas": "Keeper of the Legend",
        }
        for member in [lead, *(sm.member for sm in guild.staff_memberships.all())]:
            # The safety contract: FORMER, user-less, email-less, directory-hidden —
            # invisible to every resolver, cron, sync, and the member directory.
            assert member.status == Member.Status.FORMER
            assert member.user is None
            assert member.primary_email == ""
            assert member.hide_from_directory is True
            assert member.member_type == Member.MemberType.VOLUNTEER

    def it_seeds_no_community_events(db):
        # Published events leak onto the public calendar and Discord regardless of
        # guild.is_active — the example guild must never create any.
        guild = seed_example_guild()
        assert guild.events.count() == 0

    def it_is_idempotent(db):
        first = seed_example_guild()
        second = seed_example_guild()
        assert first.pk == second.pk
        assert Guild.objects.filter(slug=EXAMPLE_GUILD_SLUG).count() == 1
        assert second.gallery_images.count() == 3
        assert second.faq_items.count() == 3
        assert second.links.count() == 3
        assert second.announcements.count() == 3
        assert second.meeting_notes.count() == 2
        assert GuildStaffMembership.objects.filter(guild=second).count() == 5
        assert Member.objects.filter(hide_from_directory=True).count() == 6

    def it_reuses_the_first_existing_membership_plan(db):
        # Migrations seed real plans — the fictional crew borrows the first one
        # rather than minting a new row.
        before = MembershipPlan.objects.count()
        assert before > 0
        guild = seed_example_guild()
        assert guild.guild_lead.membership_plan == MembershipPlan.objects.order_by("pk").first()
        assert MembershipPlan.objects.count() == before

    def it_creates_a_plan_when_none_exists(db):
        Member.objects.all().delete()
        MembershipPlan.objects.all().delete()
        seed_example_guild()
        assert MembershipPlan.objects.filter(name="Standard").exists()

    def describe_the_management_command():
        def it_reports_the_seeded_guild(db):
            out = StringIO()
            call_command("seed_example_guild", stdout=out)
            assert "Example guild ready: Cartographers Guild" in out.getvalue()
            assert "/guilds/cartographers-guild/" in out.getvalue()
            assert "is_active=False" in out.getvalue()

    def describe_visibility():
        def it_stays_out_of_listings_but_renders_by_direct_link(client, db):
            guild = seed_example_guild()
            assert guild not in Guild.objects.directory()
            response = client.get(f"/guilds/{EXAMPLE_GUILD_SLUG}/")
            assert response.status_code == 200
            assert b"Cartographers Guild" in response.content

        def it_keeps_the_fictional_crew_out_of_the_member_directory(db):
            seed_example_guild()
            names = set(Member.objects.directory_visible().values_list("full_legal_name", flat=True))
            assert names & {"Ada Meridian", "Niko Contour", "Felix Atlas"} == set()
