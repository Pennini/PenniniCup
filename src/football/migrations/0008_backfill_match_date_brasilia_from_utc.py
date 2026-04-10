from zoneinfo import ZoneInfo

from django.db import migrations
from django.utils import timezone


def _ensure_aware(dt, default_tz):
    if dt is None:
        return None
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, default_tz)
    return dt


def backfill_match_date_brasilia(apps, schema_editor):
    Match = apps.get_model("football", "Match")
    utc_tz = ZoneInfo("UTC")
    brasilia_tz = ZoneInfo("America/Sao_Paulo")

    rows = []
    for match in Match.objects.only("id", "match_date_utc", "match_date_brasilia").iterator():
        match_date_utc = _ensure_aware(match.match_date_utc, utc_tz)
        if match_date_utc is None:
            continue

        match.match_date_brasilia = match_date_utc.astimezone(brasilia_tz)
        rows.append(match)

    if rows:
        Match.objects.bulk_update(rows, ["match_date_brasilia"])


def noop_reverse(apps, schema_editor):
    return


class Migration(migrations.Migration):
    dependencies = [
        ("football", "0007_assignthird"),
    ]

    operations = [
        migrations.RunPython(backfill_match_date_brasilia, noop_reverse),
    ]
