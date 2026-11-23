"""Remove the five doors #1307 retired: the Sponsorship Tracker, the Marketing Tracker, the wine wall, the Market
Check-in and the event flyer page.

First the data: every HubCard of those kinds and every HubGoal counting the wine wall (``bottles``,
``bottle_value``) is deleted, so no card or chip points at a page that is gone. Then the DDL drops the nine tables
(Sponsor, SponsorLogEntry, MarketingAction, WineBottle, WineryLogo, WineDonorThanks, MarketReport, VolunteerHours,
HubFlyer) and LedgerSettings' four wine wall ticket columns. The rows were exported to CSV before deploy (#1307 AC8).
Uploaded bottle photos, winery logos and flyer files stay in storage; only their rows go. The release serving during
the deploy may 500 on HQ pages for under a minute (the #1091 precedent). The reverse restores the empty tables and
columns but not the deleted rows. Four help texts that named Lee or a removed door are reworded.
"""

from django.db import migrations, models

REMOVED_CARD_KINDS = ("wine_wall", "marketing", "sponsorship", "market_checkin", "flyer")
REMOVED_GOAL_KINDS = ("bottles", "bottle_value")


def delete_removed_rows(apps, schema_editor) -> None:
    apps.get_model("hq", "HubCard").objects.filter(kind__in=REMOVED_CARD_KINDS).delete()
    apps.get_model("hq", "HubGoal").objects.filter(kind__in=REMOVED_GOAL_KINDS).delete()


def keep_removed_rows_deleted(apps, schema_editor) -> None:
    """The reverse cannot bring the deleted cards and goals back: their rows are gone, and the CSV export taken
    before the deploy (#1307 AC8) is the record. Going back leaves the hub with its four doors; an admin who wants
    an old door back adds it again on Edit HQ."""


class Migration(migrations.Migration):
    dependencies = [
        ("hq", "0018_hubcard_officers_only"),
    ]

    operations = [
        migrations.RunPython(delete_removed_rows, keep_removed_rows_deleted),
        migrations.RemoveField(
            model_name="hubflyer",
            name="hub",
        ),
        migrations.RemoveField(
            model_name="marketingaction",
            name="hub",
        ),
        migrations.RemoveField(
            model_name="marketingaction",
            name="logged_by",
        ),
        migrations.RemoveConstraint(
            model_name="marketreport",
            name="uq_marketreport_hub_member",
        ),
        migrations.RemoveField(
            model_name="sponsor",
            name="created_by",
        ),
        migrations.RemoveField(
            model_name="sponsor",
            name="hub",
        ),
        migrations.RemoveField(
            model_name="sponsorlogentry",
            name="sponsor",
        ),
        migrations.RemoveConstraint(
            model_name="volunteerhours",
            name="ck_volunteerhours_range",
        ),
        migrations.RemoveField(
            model_name="winebottle",
            name="hub",
        ),
        migrations.RemoveField(
            model_name="winebottle",
            name="logged_by",
        ),
        migrations.RemoveConstraint(
            model_name="winedonorthanks",
            name="uq_winedonorthanks_hub_key",
        ),
        migrations.RemoveConstraint(
            model_name="winerylogo",
            name="uq_winerylogo_hub_name",
        ),
        migrations.RemoveField(
            model_name="ledgersettings",
            name="pair_cents",
        ),
        migrations.RemoveField(
            model_name="ledgersettings",
            name="pairs_sold",
        ),
        migrations.RemoveField(
            model_name="ledgersettings",
            name="singles_sold",
        ),
        migrations.RemoveField(
            model_name="ledgersettings",
            name="ticket_cents",
        ),
        migrations.AlterField(
            model_name="hubcard",
            name="kind",
            field=models.CharField(
                choices=[
                    ("rsvp", "RSVP"),
                    ("volunteers", "Volunteer shifts"),
                    ("vendor_tables", "Vendor Tables"),
                    ("ledger", "PL Ledger"),
                    ("placeholder", "Not shared yet"),
                ],
                default="placeholder",
                help_text="Which page the card opens. A placeholder opens nothing and reads Not shared yet.",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="hubgoal",
            name="kind",
            field=models.CharField(
                choices=[
                    ("manual", "Typed in by hand"),
                    ("rsvps", "People going (RSVPs)"),
                    ("volunteer_spots", "Volunteer spots filled"),
                    ("member_tables", "Vendor tables confirmed"),
                    ("event_cash", "Event cash"),
                    ("guilds", "Guild demos planned"),
                ],
                default="manual",
                help_text="Where the figure comes from: typed in, or counted live from one part of the event.",
                max_length=20,
            ),
        ),
        migrations.DeleteModel(
            name="HubFlyer",
        ),
        migrations.DeleteModel(
            name="MarketingAction",
        ),
        migrations.RemoveField(
            model_name="marketreport",
            name="guild",
        ),
        migrations.RemoveField(
            model_name="marketreport",
            name="hub",
        ),
        migrations.RemoveField(
            model_name="marketreport",
            name="member",
        ),
        migrations.DeleteModel(
            name="Sponsor",
        ),
        migrations.DeleteModel(
            name="SponsorLogEntry",
        ),
        migrations.RemoveField(
            model_name="volunteerhours",
            name="hub",
        ),
        migrations.RemoveField(
            model_name="volunteerhours",
            name="member",
        ),
        migrations.DeleteModel(
            name="WineBottle",
        ),
        migrations.RemoveField(
            model_name="winedonorthanks",
            name="hub",
        ),
        migrations.RemoveField(
            model_name="winerylogo",
            name="hub",
        ),
        migrations.DeleteModel(
            name="MarketReport",
        ),
        migrations.DeleteModel(
            name="VolunteerHours",
        ),
        migrations.DeleteModel(
            name="WineDonorThanks",
        ),
        migrations.DeleteModel(
            name="WineryLogo",
        ),
        # Help texts that named Lee or a removed door (#1307).
        migrations.AlterField(
            model_name="budgetline",
            name="name",
            field=models.CharField(help_text="The line's name, e.g. Kegs or Cups and napkins.", max_length=120),
        ),
        migrations.AlterField(
            model_name="stafftime",
            name="note",
            field=models.CharField(blank=True, help_text="What they did, e.g. Called the guild leads.", max_length=200),
        ),
        migrations.AlterField(
            model_name="stafftime",
            name="who",
            field=models.CharField(help_text="Who did the work, as typed, e.g. Kate.", max_length=120),
        ),
        migrations.AlterField(
            model_name="vendortable",
            name="request_source",
            field=models.CharField(
                blank=True,
                help_text="How the request came in, e.g. Discord, In person, Kate's sign-up doc.",
                max_length=60,
            ),
        ),
    ]
