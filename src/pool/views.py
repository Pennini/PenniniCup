import logging
import re
from types import SimpleNamespace
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from src.football.models import Match, Player
from src.pool.forms import PoolBetForm
from src.pool.models import Pool, PoolBet, PoolParticipant
from src.pool.services.projection import (
    build_projected_placeholder_map,
    load_assign_third_map,
    load_persisted_group_standings,
    load_persisted_third_places,
    resolve_knockout_placeholder_team,
)
from src.pool.services.projection_queue import (
    enqueue_projection_recalc,
    has_pending_projection_recalc,
    projection_is_stale,
)
from src.pool.services.rules import PHASE_GROUP, PHASE_KNOCKOUT, phase_for_match

logger = logging.getLogger(__name__)

STAGE_R32 = "R32"
STAGE_R16 = "R16"
STAGE_QF = "QF"
STAGE_SF = "SF"
STAGE_FINAL = "FINAL"
STAGE_THIRD = "THIRD"

KNOCKOUT_STAGE_ORDER = [STAGE_R32, STAGE_R16, STAGE_QF, STAGE_SF]
KNOCKOUT_LABELS = {
    STAGE_R32: "32 Avos",
    STAGE_R16: "Oitavas",
    STAGE_QF: "Quartas",
    STAGE_SF: "Semifinal",
}


def _ensure_participant_bets(participant, matches):
    if not participant.can_bet():
        return {}

    existing = {bet.match_id: bet for bet in participant.bets.select_related("match").all()}
    missing_rows = [
        PoolBet(participant=participant, match=match, is_active=False) for match in matches if match.id not in existing
    ]

    if missing_rows:
        PoolBet.objects.bulk_create(missing_rows)
        existing = {bet.match_id: bet for bet in participant.bets.select_related("match").all()}

    return existing


_WINNER_PLACEHOLDER_PATTERN = re.compile(r"^W(\d+)$")
_LOSER_PLACEHOLDER_PATTERN = re.compile(r"^RU(\d+)$")


def _build_winners_map(matches, bets_by_match_id):
    winners_map = {}
    for match in matches:
        bet = bets_by_match_id.get(match.id)
        if bet and bet.is_active and bet.winner_pred_id:
            winners_map[match.match_number] = bet.winner_pred
            continue
        if (
            bet
            and bet.is_active
            and bet.home_score_pred is not None
            and bet.away_score_pred is not None
            and match.home_team_id
            and match.away_team_id
        ):
            if bet.home_score_pred > bet.away_score_pred:
                winners_map[match.match_number] = match.home_team
                continue
            if bet.away_score_pred > bet.home_score_pred:
                winners_map[match.match_number] = match.away_team
                continue
        if match.winner_id:
            winners_map[match.match_number] = match.winner
    return winners_map


def _infer_advancing_team(match, bet, home_team, away_team):
    if match.winner_id and match.winner:
        return match.winner

    if not bet or not bet.is_active:
        return None

    if bet.winner_pred_id:
        return bet.winner_pred

    if home_team is None or away_team is None or bet.home_score_pred is None or bet.away_score_pred is None:
        return None

    if bet.home_score_pred > bet.away_score_pred:
        return home_team
    if bet.away_score_pred > bet.home_score_pred:
        return away_team

    return None


def _infer_losing_team(winner_team, home_team, away_team):
    if winner_team is None or home_team is None or away_team is None:
        return None
    if winner_team.id == home_team.id:
        return away_team
    if winner_team.id == away_team.id:
        return home_team
    return None


def _resolve_match_team_from_placeholder(placeholder, projected_slots, assign_third_map, winners_map, losers_map):
    normalized = (placeholder or "").replace(" ", "").upper()
    winner_match = _WINNER_PLACEHOLDER_PATTERN.match(normalized)
    if winner_match:
        return winners_map.get(int(winner_match.group(1)))
    loser_match = _LOSER_PLACEHOLDER_PATTERN.match(normalized)
    if loser_match:
        return losers_map.get(int(loser_match.group(1)))
    return resolve_knockout_placeholder_team(normalized, projected_slots, assign_third_map)


def _make_pairs(items):
    return [items[index : index + 2] for index in range(0, len(items), 2)]


def _normalize_stage_key(stage):
    if not stage:
        return ""

    stage_name = (stage.name or "").upper().replace("-", " ").strip()

    if "SEMI" in stage_name or "SF" in stage_name:
        return STAGE_SF
    if "QUART" in stage_name or "QF" in stage_name:
        return STAGE_QF
    if "R16" in stage_name or "OITAV" in stage_name or "ROUND OF 16" in stage_name:
        return STAGE_R16
    if "R32" in stage_name or "32 AVOS" in stage_name or "SEGUNDAS DE FINAL" in stage_name:
        return STAGE_R32
    if "DECIS" in stage_name and "3" in stage_name:
        return STAGE_THIRD
    if "TERCE" in stage_name and "LUGAR" in stage_name:
        return STAGE_THIRD
    if stage_name == "FINAL":
        return STAGE_FINAL
    if "FINAL" in stage_name and "SEMI" not in stage_name and "QUART" not in stage_name and "OITAV" not in stage_name:
        return STAGE_FINAL

    return ""


def _build_projected_knockout_payload(knockout_rows):
    grouped_matches = {stage_key: [] for stage_key in KNOCKOUT_STAGE_ORDER}
    final_match = None
    third_place_match = None
    knockout_by_number = {}

    for row in knockout_rows:
        stage_key = _normalize_stage_key(row["match"].stage)
        projected_match = SimpleNamespace(
            source_match_number=row["match"].match_number,
            source_stage=row["match"].stage,
            source_home_placeholder=row["match"].home_placeholder,
            source_away_placeholder=row["match"].away_placeholder,
            home_team=row["home_team"],
            away_team=row["away_team"],
            home_team_id=row["home_team"].id if row["home_team"] else None,
            away_team_id=row["away_team"].id if row["away_team"] else None,
            home_score=row["bet"].home_score_pred if row["bet"] else None,
            away_score=row["bet"].away_score_pred if row["bet"] else None,
            winner_id=row["bet"].winner_pred_id if row["bet"] else None,
            home_penalty_score=None,
            away_penalty_score=None,
        )

        if stage_key in grouped_matches:
            grouped_matches[stage_key].append(projected_match)
            knockout_by_number[row["match"].match_number] = projected_match
        elif stage_key == STAGE_FINAL and final_match is None:
            final_match = projected_match
        elif stage_key == STAGE_THIRD and third_place_match is None:
            third_place_match = projected_match

    def _winner_source(placeholder):
        normalized = (placeholder or "").replace(" ", "").upper()
        winner_match = _WINNER_PLACEHOLDER_PATTERN.match(normalized)
        if not winner_match:
            return None
        return int(winner_match.group(1))

    children_by_number = {}
    for number, match in knockout_by_number.items():
        children = []
        for placeholder in (match.source_home_placeholder, match.source_away_placeholder):
            child_number = _winner_source(placeholder)
            if child_number is not None and child_number in knockout_by_number:
                children.append(child_number)
        children_by_number[number] = tuple(children)

    def _collect_descendants(root_number):
        if root_number is None:
            return set()

        collected = set()
        stack = [root_number]
        while stack:
            current = stack.pop()
            if current in collected:
                continue
            if current not in knockout_by_number:
                continue
            collected.add(current)
            stack.extend(children_by_number.get(current, ()))
        return collected

    left_root = _winner_source(final_match.source_home_placeholder) if final_match else None
    right_root = _winner_source(final_match.source_away_placeholder) if final_match else None

    left_numbers = _collect_descendants(left_root)
    right_numbers = _collect_descendants(right_root)

    fallback_order = {
        match.source_match_number: idx
        for idx, match in enumerate(sorted(knockout_by_number.values(), key=lambda m: m.source_match_number))
    }

    def _sort_side(side_numbers, root_number):
        if not side_numbers:
            return []

        leaf_order = {}
        counter = [0]

        def _walk(number):
            if number in leaf_order:
                return leaf_order[number]

            children = [child for child in children_by_number.get(number, ()) if child in side_numbers]
            if not children:
                leaf_order[number] = counter[0]
                counter[0] += 1
                return leaf_order[number]

            child_positions = [_walk(child) for child in children]
            leaf_order[number] = min(child_positions)
            return leaf_order[number]

        if root_number in side_numbers:
            _walk(root_number)

        for number in sorted(side_numbers):
            if number not in leaf_order:
                leaf_order[number] = counter[0]
                counter[0] += 1

        sorted_numbers = sorted(
            side_numbers,
            key=lambda number: (leaf_order[number], fallback_order.get(number, 9999)),
        )
        return [knockout_by_number[number] for number in sorted_numbers]

    left_sorted = _sort_side(left_numbers, left_root)
    right_sorted = _sort_side(right_numbers, right_root)

    active_stages = [stage_key for stage_key in KNOCKOUT_STAGE_ORDER if grouped_matches[stage_key]]
    bracket_left = []
    bracket_right = []

    for stage_key in active_stages:
        stage_matches = grouped_matches[stage_key]
        left_matches = [match for match in left_sorted if _normalize_stage_key(match.source_stage) == stage_key]
        right_matches = [match for match in right_sorted if _normalize_stage_key(match.source_stage) == stage_key]

        if not left_matches and not right_matches:
            stage_matches_sorted = sorted(stage_matches, key=lambda match: match.source_match_number)
            half = len(stage_matches_sorted) // 2
            left_matches = stage_matches_sorted[:half]
            right_matches = stage_matches_sorted[half:]

        bracket_left.append(
            {
                "stage": stage_key,
                "label": KNOCKOUT_LABELS[stage_key],
                "pairs": _make_pairs(left_matches),
                "is_outermost": False,
            }
        )
        bracket_right.append(
            {
                "stage": stage_key,
                "label": KNOCKOUT_LABELS[stage_key],
                "pairs": _make_pairs(right_matches),
                "is_outermost": False,
            }
        )

    if bracket_left:
        bracket_left[0]["is_outermost"] = True

    bracket_right = list(reversed(bracket_right))
    if bracket_right:
        bracket_right[-1]["is_outermost"] = True

    max_matches_side = max((sum(len(pair) for pair in round_data["pairs"]) for round_data in bracket_left), default=2)
    bracket_height = max(max_matches_side * 78, 280)

    return {
        "bracket_left": bracket_left,
        "bracket_right": bracket_right,
        "final_match": final_match,
        "third_place_match": third_place_match,
        "bracket_height": bracket_height,
    }


def _join_pool_with_token(request, pool, invite_token_value):
    token_obj, token_error = pool.validate_invite_token(invite_token_value)
    if token_error:
        messages.error(request, token_error)
        return None

    with transaction.atomic():
        participant, created = PoolParticipant.objects.get_or_create(
            pool=pool,
            user=request.user,
            defaults={"is_active": True},
        )

        if created:
            consumed = pool.consume_invite_token(token_obj)
            if not consumed:
                messages.error(request, "Nao foi possivel consumir o token. Tente novamente.")
                transaction.set_rollback(True)
                return None

    if created:
        messages.success(request, "Voce entrou no bolao com sucesso.")
    else:
        messages.info(request, "Voce ja participa deste bolao.")

    return participant


def _top_scorer_options_for_pool(pool):
    return (
        Player.objects.filter(team__group__stage__season=pool.season)
        .select_related("team")
        .order_by("name")
        .distinct()
    )


@login_required
def pool_list(request):
    participations = (
        PoolParticipant.objects.filter(user=request.user, is_active=True)
        .select_related("pool", "pool__season")
        .order_by("pool__name")
    )

    rows = []
    for participant in participations:
        pool = participant.pool
        rows.append(
            {
                "pool": pool,
                "can_bet": participant.can_bet(),
                "group_locked": pool.is_phase_locked(PHASE_GROUP),
                "knockout_locked": pool.is_phase_locked(PHASE_KNOCKOUT),
            }
        )

    return render(request, "pool/list.html", {"rows": rows})


@login_required
@require_http_methods(["POST"])
def open_pool(request):
    pool_slug = (request.POST.get("pool_slug") or "").strip()
    open_target = (request.POST.get("open_target") or "bets").strip().lower()
    if not pool_slug:
        messages.error(request, "Selecione um bolao para abrir.")
        return redirect("pool:list")

    participant_exists = PoolParticipant.objects.filter(
        user=request.user, is_active=True, pool__slug=pool_slug
    ).exists()
    if not participant_exists:
        messages.error(request, "Voce nao esta inscrito neste bolao.")
        return redirect("pool:list")

    if open_target == "ranking":
        return redirect("pool:ranking", slug=pool_slug)

    return redirect("pool:detail", slug=pool_slug)


@login_required
def pool_detail(request, slug):
    pool = get_object_or_404(Pool.objects.select_related("season"), slug=slug, is_active=True)
    participant = get_object_or_404(PoolParticipant, pool=pool, user=request.user)
    active_tab = (request.GET.get("tab") or "bets").strip()
    if active_tab not in ("bets", "classification", "knockout"):
        return redirect(f"{request.path}?tab=bets")

    matches = list(
        Match.objects.filter(season=pool.season)
        .select_related("stage", "group", "home_team", "away_team")
        .order_by("match_number", "match_date_brasilia")
    )
    bets_by_match_id = _ensure_participant_bets(participant=participant, matches=matches)

    if projection_is_stale(participant):
        enqueue_projection_recalc(participant)

    projected_groups = load_persisted_group_standings(participant=participant)
    third_rows = load_persisted_third_places(participant=participant)

    projected = build_projected_placeholder_map(projected_groups=projected_groups, third_rows=third_rows)
    qualified_groups = sorted([row["group"].name for row in third_rows if row["is_qualified"]])
    assign_third_map = load_assign_third_map(season=pool.season, qualified_groups=qualified_groups)
    winners_map = _build_winners_map(matches=matches, bets_by_match_id=bets_by_match_id)
    losers_map = {}

    match_rows = []
    group_rows = []
    knockout_rows = []
    for match in matches:
        phase = phase_for_match(match)
        home_team = match.home_team
        away_team = match.away_team
        bet = bets_by_match_id.get(match.id)

        if phase == PHASE_KNOCKOUT:
            if home_team is None:
                home_team = _resolve_match_team_from_placeholder(
                    placeholder=match.home_placeholder,
                    projected_slots=projected,
                    assign_third_map=assign_third_map,
                    winners_map=winners_map,
                    losers_map=losers_map,
                )
            if away_team is None:
                away_team = _resolve_match_team_from_placeholder(
                    placeholder=match.away_placeholder,
                    projected_slots=projected,
                    assign_third_map=assign_third_map,
                    winners_map=winners_map,
                    losers_map=losers_map,
                )

        row = {
            "match": match,
            "phase": phase,
            "home_team": home_team,
            "away_team": away_team,
            "bet": bet,
            "locked": pool.is_phase_locked(phase),
        }
        match_rows.append(row)

        if phase == PHASE_GROUP:
            group_rows.append(row)
        else:
            knockout_rows.append(row)

        advancing_team = _infer_advancing_team(
            match=match,
            bet=bet,
            home_team=home_team,
            away_team=away_team,
        )
        if advancing_team is not None:
            winners_map[match.match_number] = advancing_team
            losing_team = _infer_losing_team(
                winner_team=advancing_team,
                home_team=home_team,
                away_team=away_team,
            )
            if losing_team is not None:
                losers_map[match.match_number] = losing_team

    projected_knockout = _build_projected_knockout_payload(knockout_rows=knockout_rows)
    top_scorer_options = _top_scorer_options_for_pool(pool)

    show_reprocess_notice = (request.GET.get("reprocess") or "").strip() == "1"

    context = {
        "pool": pool,
        "participant": participant,
        "active_tab": active_tab,
        "match_rows": match_rows,
        "group_rows": group_rows,
        "knockout_rows": knockout_rows,
        "projected_groups": projected_groups,
        "can_bet": participant.can_bet(),
        "group_locked": pool.is_phase_locked(PHASE_GROUP),
        "knockout_locked": pool.is_phase_locked(PHASE_KNOCKOUT),
        "projection_pending": has_pending_projection_recalc(participant),
        "show_reprocess_notice": show_reprocess_notice,
        "top_scorer_options": top_scorer_options,
        "page_mode": "result",
        **projected_knockout,
    }
    return render(request, "pool/detail.html", context)


@login_required
@require_http_methods(["POST"])
def join_pool(request, slug):
    pool = get_object_or_404(Pool, slug=slug, is_active=True)
    invite_token_value = (request.POST.get("invite_token") or "").strip()

    if not invite_token_value:
        messages.error(request, "Informe um token de convite para entrar neste bolao.")
        return redirect("pool:list")

    participant = _join_pool_with_token(request, pool, invite_token_value)
    if participant is None:
        return redirect("pool:list")

    return redirect("pool:detail", slug=pool.slug)


@login_required
@require_http_methods(["POST"])
def join_pool_by_token(request):
    invite_token_value = (request.POST.get("invite_token") or "").strip()
    if not invite_token_value:
        messages.error(request, "Informe um token de convite para entrar em um bolao.")
        return redirect("penninicup:index")

    try:
        UUID(invite_token_value)
    except (TypeError, ValueError):
        messages.error(request, "Token invalido ou sem bolao associado.")
        return redirect("penninicup:index")

    from src.accounts.models import InviteToken

    token_obj = InviteToken.objects.filter(token=invite_token_value).select_related("pool").first()
    if not token_obj or token_obj.pool_id is None:
        messages.error(request, "Token invalido ou sem bolao associado.")
        return redirect("penninicup:index")

    pool = token_obj.pool
    if not pool.is_active:
        messages.error(request, "O bolao deste token esta inativo.")
        return redirect("penninicup:index")

    participant = _join_pool_with_token(request, pool, invite_token_value)
    if participant is None:
        return redirect("penninicup:index")

    return redirect("pool:detail", slug=pool.slug)


@login_required
@require_http_methods(["POST"])
def save_bet(request, slug, match_id):
    pool = get_object_or_404(Pool, slug=slug, is_active=True)
    participant = get_object_or_404(PoolParticipant, pool=pool, user=request.user, is_active=True)
    match = get_object_or_404(Match.objects.select_related("stage"), id=match_id, season=pool.season)

    _ensure_participant_bets(participant=participant, matches=[match])

    existing_bet = PoolBet.objects.filter(participant=participant, match=match).first()
    if existing_bet is None:
        existing_bet = PoolBet(participant=participant, match=match)

    form = PoolBetForm(request.POST, instance=existing_bet, match=match)
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"

    if not form.is_valid():
        if is_ajax:
            return JsonResponse({"ok": False, "errors": form.errors.get_json_data()}, status=400)
        for _, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
        return redirect("pool:detail", slug=pool.slug)

    bet = form.save(commit=False)
    bet.participant = participant
    bet.match = match

    try:
        with transaction.atomic():
            bet.full_clean()
            bet.save()
            if phase_for_match(match) == PHASE_GROUP:
                enqueue_projection_recalc(participant)
    except Exception as exc:
        logger.warning("Falha ao salvar palpite: participant=%s match=%s error=%s", participant.id, match.id, str(exc))
        if is_ajax:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        messages.error(request, str(exc))
        return redirect("pool:detail", slug=pool.slug)

    if is_ajax:
        return JsonResponse(
            {
                "ok": True,
                "is_active": bet.is_active,
                "winner_pred_id": bet.winner_pred_id,
                "home_score_pred": bet.home_score_pred,
                "away_score_pred": bet.away_score_pred,
                "projection_pending": has_pending_projection_recalc(participant),
            }
        )

    messages.success(request, "Palpite salvo com sucesso.")
    return redirect("pool:detail", slug=pool.slug)


@login_required
@require_http_methods(["POST"])
def save_bets_bulk(request, slug):
    pool = get_object_or_404(Pool, slug=slug, is_active=True)
    participant = get_object_or_404(PoolParticipant, pool=pool, user=request.user, is_active=True)

    if not participant.can_bet():
        messages.error(request, "Participante sem permissao para palpitar.")
        return redirect("pool:detail", slug=pool.slug)

    matches = list(
        Match.objects.filter(season=pool.season)
        .select_related("stage")
        .order_by("match_number", "match_date_brasilia")
    )
    existing_bets = _ensure_participant_bets(participant=participant, matches=matches)

    saved_count = 0
    saved_group_count = 0
    error_count = 0
    top_scorer_changed = False

    if not pool.is_phase_locked(PHASE_GROUP):
        top_scorer_value = (request.POST.get("top_scorer_pred") or "").strip()
        top_scorer_before = participant.top_scorer_pred_id
        if top_scorer_value:
            selected_player = _top_scorer_options_for_pool(pool).filter(id=top_scorer_value).first()
            if selected_player is None:
                messages.error(request, "Artilheiro invalido para esta temporada.")
                error_count += 1
            else:
                participant.top_scorer_pred = selected_player
        else:
            participant.top_scorer_pred = None

        if participant.top_scorer_pred_id != top_scorer_before:
            participant.save(update_fields=["top_scorer_pred"])
            top_scorer_changed = True

    for match in matches:
        phase = phase_for_match(match)
        if pool.is_phase_locked(phase):
            continue

        home_key = f"match_{match.id}_home_score_pred"
        away_key = f"match_{match.id}_away_score_pred"
        winner_key = f"match_{match.id}_winner_pred"

        home_value = (request.POST.get(home_key) or "").strip()
        away_value = (request.POST.get(away_key) or "").strip()
        winner_value = (request.POST.get(winner_key) or "").strip()

        payload = {
            "home_score_pred": home_value,
            "away_score_pred": away_value,
            "winner_pred": winner_value,
        }

        bet = existing_bets.get(match.id)
        before_state = (
            bet.home_score_pred if bet else None,
            bet.away_score_pred if bet else None,
            bet.winner_pred_id if bet else None,
            bet.is_active if bet else False,
        )

        if bet is None:
            bet = PoolBet(participant=participant, match=match)

        form = PoolBetForm(payload, instance=bet, match=match)

        if not form.is_valid():
            for _, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"Jogo {match.match_number}: {error}")
            error_count += 1
            continue

        bet_obj = form.save(commit=False)
        bet_obj.participant = participant
        bet_obj.match = match

        try:
            with transaction.atomic():
                bet_obj.full_clean()
                bet_obj.save()
            after_state = (
                bet_obj.home_score_pred,
                bet_obj.away_score_pred,
                bet_obj.winner_pred_id,
                bet_obj.is_active,
            )
            if after_state != before_state:
                saved_count += 1
                if phase == PHASE_GROUP:
                    saved_group_count += 1
        except Exception as exc:
            logger.warning(
                "Falha ao salvar palpite em lote: participant=%s match=%s error=%s",
                participant.id,
                match.id,
                str(exc),
            )
            messages.error(request, f"Jogo {match.match_number}: {str(exc)}")
            error_count += 1

    if saved_group_count:
        enqueue_projection_recalc(participant)

    total_changes = saved_count + (1 if top_scorer_changed else 0)

    if not total_changes and not error_count:
        messages.info(request, "Nenhuma alteracao para salvar.")
    elif saved_group_count and not error_count:
        messages.warning(
            request,
            "Palpites salvos. Revise os jogos de mata-mata: os classificados podem ter sido alterados.",
        )
    elif total_changes and not error_count:
        messages.success(request, f"{total_changes} alteracao(oes) salva(s) com sucesso.")
    elif total_changes and error_count:
        messages.warning(request, "Alguns palpites foram salvos, mas houve erros em outros jogos.")

    return redirect("pool:detail", slug=pool.slug)
