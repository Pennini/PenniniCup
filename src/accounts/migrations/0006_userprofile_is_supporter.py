from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_userprofile_profile_image_imagefield"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="is_supporter",
            field=models.BooleanField(default=False),
        ),
    ]
