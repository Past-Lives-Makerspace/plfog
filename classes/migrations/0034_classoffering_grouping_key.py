from django.db import migrations, models


def backfill_grouping_keys(apps, schema_editor):
    """Derive grouping_key for every existing offering so legacy dated copies collapse."""
    from classes.grouping import grouping_key_for

    ClassOffering = apps.get_model("classes", "ClassOffering")
    to_update = []
    for offering in ClassOffering.objects.all().only("id", "title", "category_id", "grouping_key"):
        key = grouping_key_for(offering.title, offering.category_id)
        if offering.grouping_key != key:
            offering.grouping_key = key
            to_update.append(offering)
    if to_update:
        ClassOffering.objects.bulk_update(to_update, ["grouping_key"])


def clear_grouping_keys(apps, schema_editor):
    """Reverse: clear the derived column (it is fully regenerable from title + category)."""
    ClassOffering = apps.get_model("classes", "ClassOffering")
    ClassOffering.objects.update(grouping_key="")


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0033_category_hero_crop_h_category_hero_crop_w_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="classoffering",
            name="grouping_key",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text=(
                    "Links the same class offered on multiple dates into one public catalog card. "
                    "Derived from the normalized title + category on save; empty offerings stand alone."
                ),
                max_length=255,
            ),
        ),
        migrations.RunPython(backfill_grouping_keys, clear_grouping_keys),
    ]
