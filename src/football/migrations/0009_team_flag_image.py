from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("football", "0008_backfill_match_date_brasilia_from_utc"),
    ]

    operations = [
        migrations.AddField(
            model_name="team",
            name="flag_image",
            field=models.FileField(blank=True, upload_to="flags/"),
        ),
    ]
