import re

PHASE_GROUP = "GROUP"
PHASE_KNOCKOUT = "KNOCKOUT"

POOL_TYPE_1 = 1  # Classificados travados, placar livre — scoring posicional
POOL_TYPE_2 = 2  # Palpite progressivo — scoring normal com unlock por partida


def normalize_stage_key(stage):
    if not stage:
        return ""

    stage_name = (stage.name or "").upper().replace("-", " ").strip()
    if "GROUP" in stage_name or "GRUPO" in stage_name or "PRIMEIRA FASE" in stage_name:
        return "GROUP"
    if "SEMI" in stage_name or "SF" in stage_name:
        return "SF"
    if "QUART" in stage_name or "QF" in stage_name:
        return "QF"
    if "R16" in stage_name or "OITAV" in stage_name or "ROUND OF 16" in stage_name:
        return "R16"
    if "R32" in stage_name or "32 AVOS" in stage_name or "SEGUNDAS DE FINAL" in stage_name:
        return "R32"
    if "DECIS" in stage_name and "3" in stage_name:
        return "THIRD"
    if "TERCE" in stage_name and "LUGAR" in stage_name:
        return "THIRD"
    if stage_name == "FINAL":
        return "FINAL"
    if "FINAL" in stage_name and "SEMI" not in stage_name and "QUART" not in stage_name and "OITAV" not in stage_name:
        return "FINAL"
    return ""


def phase_for_match(match):
    stage_key = normalize_stage_key(match.stage)
    if stage_key == "GROUP":
        return PHASE_GROUP
    return PHASE_KNOCKOUT


def _get_parent_match_number(placeholder):
    """Extract match_number from W<N> or RU<N> placeholder. Returns None for group placeholders."""
    m = re.match(r"^(?:W|RU)(\d+)$", (placeholder or "").strip().upper())
    return int(m.group(1)) if m else None


def get_feeder_r32_matches(match, season):
    """Return all R32-level matches that feed into the given knockout match.

    For R32, returns [match]. For deeper rounds, recursively follows W<N>/RU<N>
    placeholders until R32 is reached. Used for Tipo 2 progressive unlock logic.
    """
    from src.football.models import Match as FootballMatch

    stage_key = normalize_stage_key(match.stage)

    if stage_key in ("GROUP", ""):
        return []

    if stage_key == "R32":
        return [match]

    feeder = []
    for placeholder in (match.home_placeholder, match.away_placeholder):
        parent_num = _get_parent_match_number(placeholder)
        if parent_num is None:
            continue
        try:
            parent = FootballMatch.objects.select_related("stage").get(season=season, match_number=parent_num)
            feeder.extend(get_feeder_r32_matches(parent, season))
        except FootballMatch.DoesNotExist:
            pass

    return feeder


def is_type2_bet_open(match, season):
    """Tipo 2: knockout bet is open when all upstream R32 teams are known and match hasn't started."""
    from django.utils import timezone

    feeder_matches = get_feeder_r32_matches(match, season)
    if not feeder_matches:
        return False

    all_teams_known = all(m.home_team_id is not None and m.away_team_id is not None for m in feeder_matches)
    return all_teams_known and match.match_date_brasilia > timezone.now()
