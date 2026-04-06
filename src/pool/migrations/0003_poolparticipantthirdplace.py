from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("football", "0007_assignthird"),
        ("pool", "0002_poolparticipantstanding"),
    ]

    operations = [
        migrations.CreateModel(
            name="PoolParticipantThirdPlace",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("position_global", models.PositiveSmallIntegerField()),
                ("points", models.PositiveSmallIntegerField(default=0)),
                ("goal_difference", models.SmallIntegerField(default=0)),
                ("goals_for", models.PositiveSmallIntegerField(default=0)),
                ("score", models.IntegerField(default=0)),
                ("is_qualified", models.BooleanField(default=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "group",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="participant_third_places",
                        to="football.group",
                    ),
                ),
                (
                    "participant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="projected_third_places",
                        to="pool.poolparticipant",
                    ),
                ),
                (
                    "team",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="participant_third_places",
                        to="football.team",
                    ),
                ),
            ],
            options={
                "ordering": ["position_global", "group__name", "team__code"],
                "unique_together": {("participant", "group")},
            },
        ),
    ]
