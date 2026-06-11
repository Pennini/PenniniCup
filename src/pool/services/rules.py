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


def get_knockout_global_lock_time(season):
    """Tipo 2: deadline único de todo o mata-mata = kickoff do 1o jogo de mata-mata (R32)."""
    from src.football.models import Match as FootballMatch

    dates = [
        m.match_date_brasilia
        for m in FootballMatch.objects.filter(season=season).select_related("stage")
        if normalize_stage_key(m.stage) not in ("GROUP", "")
    ]
    return min(dates) if dates else None


def _bet_has_winner(bet):
    """Palpite define um classificado? (winner_pred explícito ou placar decisivo)."""
    if bet.winner_pred_id is not None:
        return True
    if bet.home_score_pred is None or bet.away_score_pred is None:
        return False
    return bet.home_score_pred != bet.away_score_pred


def is_type2_bet_open(match, season, participant=None):
    """Tipo 2: abertura progressiva por jogo, com trava global no 1o jogo de mata-mata.

    - Trava global: nada de mata-mata abre a partir do kickoff do 1o jogo de R32.
    - R32: abre quando os 2 times reais da fase de grupos estão definidos.
    - R16+: abre quando o participante já palpitou os 2 feeders imediatos (a projeção
      produz os 2 times do jogo). Sem participant não há projeção, logo fechado.
    """
    from django.utils import timezone

    stage_key = normalize_stage_key(match.stage)
    if stage_key in ("GROUP", ""):
        return False

    lock_time = get_knockout_global_lock_time(season)
    if lock_time is not None and timezone.now() >= lock_time:
        return False

    if stage_key == "R32":
        return match.home_team_id is not None and match.away_team_id is not None

    if participant is None:
        return False

    for placeholder in (match.home_placeholder, match.away_placeholder):
        parent_num = _get_parent_match_number(placeholder)
        if parent_num is None:
            return False
        parent_bet = (
            participant.bets.filter(match__season=season, match__match_number=parent_num, is_active=True)
            .only("home_score_pred", "away_score_pred", "winner_pred")
            .first()
        )
        if parent_bet is None or not _bet_has_winner(parent_bet):
            return False
    return True
