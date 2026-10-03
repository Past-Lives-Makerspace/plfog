# The second half of the two phase removal 0196 began (#502, STANDARDS.md section 10).
# 0196 made ``external_signup_url`` nullable on ``membership_guildorientationsettings``
# and ``membership_orientationtype`` and took both out of Django's state while the
# release carrying it was still serving; that release is live on production, so no
# running code reads or writes the columns any more and the real DROP COLUMN is safe.
# The reverse adds both back as nullable varchar(500), the shape 0196 left them in,
# so rolling back lands on exactly the state 0196 produced.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0198_equipment_space_kind_and_space_manager"),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE membership_guildorientationsettings DROP COLUMN external_signup_url;",
            reverse_sql=(
                "ALTER TABLE membership_guildorientationsettings ADD COLUMN external_signup_url varchar(500) NULL;"
            ),
        ),
        migrations.RunSQL(
            sql="ALTER TABLE membership_orientationtype DROP COLUMN external_signup_url;",
            reverse_sql="ALTER TABLE membership_orientationtype ADD COLUMN external_signup_url varchar(500) NULL;",
        ),
    ]
