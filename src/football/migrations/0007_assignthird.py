from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("football", "0006_official"),
    ]

    operations = [
        migrations.CreateModel(
            name="AssignThird",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("groups_key", models.CharField(max_length=64)),
                ("placeholder", models.CharField(max_length=50)),
                ("third_group", models.CharField(max_length=5)),
                ("create_date", models.DateTimeField(auto_now_add=True)),
                ("update_date", models.DateTimeField(auto_now=True)),
                (
                    "season",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="assign_third_rules",
                        to="football.season",
                    ),
                ),
            ],
            options={
                "ordering": ["season", "groups_key", "placeholder"],
                "unique_together": {("season", "groups_key", "placeholder")},
            },
        ),
    ]
