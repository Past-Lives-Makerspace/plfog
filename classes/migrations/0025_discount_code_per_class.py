from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("classes", "0024_category_icon_svg"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="discountcode",
            name="class_offering",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="discount_codes",
                to="classes.classoffering",
                help_text=(
                    "When set, this code only applies to that one class. "
                    "When null, the code is global and any class can use it."
                ),
            ),
        ),
        migrations.AddField(
            model_name="discountcode",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
                help_text="User who created this code (audit + lets instructors manage their own codes).",
            ),
        ),
        migrations.AddField(
            model_name="discountcode",
            name="auto_apply",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "When on, the code is automatically applied for any eligible registrant. "
                    "Useful for class-scoped promotional pricing without making customers type a code."
                ),
            ),
        ),
    ]
