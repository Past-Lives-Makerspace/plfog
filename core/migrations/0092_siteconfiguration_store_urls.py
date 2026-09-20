# #467: the two store listings behind every "Get the app" badge. Additive, so the old
# release keeps serving while it applies (STANDARDS §10).

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0091_alter_siteconfiguration_member_directory_public"),
    ]

    operations = [
        migrations.AddField(
            model_name="siteconfiguration",
            name="google_play_url",
            field=models.URLField(
                blank=True,
                default="https://play.google.com/store/apps/details?id=app.pastlives.hub",
                help_text="The app's Google Play listing. Blank means not launched: the badge is left out everywhere.",
                verbose_name="Google Play URL",
            ),
        ),
        migrations.AddField(
            model_name="siteconfiguration",
            name="app_store_url",
            field=models.URLField(
                blank=True,
                default="https://apps.apple.com/us/app/past-lives-makerspace/id6796557084",
                help_text="The app's App Store listing. Blank means not launched: the badge is left out everywhere and the copy says iOS is coming soon.",
                verbose_name="App Store URL",
            ),
        ),
    ]
