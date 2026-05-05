import src.accounts.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_userprofile_profile_image"),
    ]

    operations = [
        migrations.AlterField(
            model_name="userprofile",
            name="profile_image",
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to="profiles/",
                validators=[src.accounts.models._validate_profile_image],
            ),
        ),
    ]
