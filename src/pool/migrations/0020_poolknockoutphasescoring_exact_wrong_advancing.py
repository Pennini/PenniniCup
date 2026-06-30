from django.db import migrations, models


def populate_exact_wrong_advancing(apps, schema_editor):
    PoolKnockoutPhaseScoring = apps.get_model("pool", "PoolKnockoutPhaseScoring")
    for row in PoolKnockoutPhaseScoring.objects.all():
        row.exact_wrong_advancing = max(row.exact - row.advancing_only, 0)
        row.save(update_fields=["exact_wrong_advancing"])


class Migration(migrations.Migration):
    dependencies = [
        ("pool", "0019_alter_poolknockoutphasescoring_advancing_goals_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="poolknockoutphasescoring",
            name="exact_wrong_advancing",
            field=models.PositiveSmallIntegerField(default=0),
            preserve_default=False,
        ),
        migrations.RunPython(populate_exact_wrong_advancing, migrations.RunPython.noop),
    ]
