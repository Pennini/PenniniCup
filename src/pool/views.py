import logging
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

from src.football.models import Match, Player
from src.pool.forms import PoolBetForm
from src.pool.models import Pool, PoolBet, PoolParticipant
from src.pool.services.context_builder import build_pool_participant_view_context as build_pool_context_service
from src.pool.services.projection_queue import (
    enqueue_projection_recalc,
    has_pending_projection_recalc,
)
from src.pool.services.rules import PHASE_GROUP, PHASE_KNOCKOUT, phase_for_match

logger = logging.getLogger(__name__)


def _friendly_save_bet_error(exc):
    detail = ""
    if isinstance(exc, ValidationError):
        if hasattr(exc, "messages") and exc.messages:
            detail = " ".join(str(message) for message in exc.messages)
        else:
            detail = str(exc)
    else:
        detail = str(exc)

    lowered = detail.lower()
    if "janela de palpites" in lowered:
        return "Fase de palpites fechada."
    if "informe o placar" in lowered:
        return "Preencha todos os campos."

    return "Erro interno, tente novamente."


def _ensure_participant_bets(participant, matches, *, can_bet, existing_bets=None):
    if not can_bet:
        return {}

    if existing_bets is None:
        existing = {bet.match_id: bet for bet in participant.bets.select_related("match").all()}
    else:
        existing = {bet.match_id: bet for bet in existing_bets}

    missing_rows = [
        PoolBet(participant=participant, match=match, is_active=False) for match in matches if match.id not in existing
    ]

    if missing_rows:
        PoolBet.objects.bulk_create(missing_rows)
        for bet in missing_rows:
            existing[bet.match_id] = bet

    return existing


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
                messages.error(request, "Não foi possível consumir o token. Tente novamente.")
                transaction.set_rollback(True)
                return None

    if created:
        messages.success(request, "Você entrou no bolão com sucesso.")
    else:
        messages.info(request, "Você já participa deste bolão.")

    return participant


def _top_scorer_options_for_pool(pool):
    return (
        Player.objects.filter(team__group__stage__season=pool.season)
        .select_related("team")
        .order_by("name")
        .distinct()
    )


def build_pool_participant_view_context(*, pool, participant, ensure_bets=True):
    return build_pool_context_service(pool=pool, participant=participant, ensure_bets=ensure_bets)


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
        messages.error(request, "Selecione um bolão para abrir.")
        return redirect("pool:list")

    participant_exists = PoolParticipant.objects.filter(
        user=request.user, is_active=True, pool__slug=pool_slug
    ).exists()
    if not participant_exists:
        messages.error(request, "Você não está inscrito neste bolão.")
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
    pool_context = build_pool_participant_view_context(pool=pool, participant=participant, ensure_bets=True)

    show_reprocess_notice = (request.GET.get("reprocess") or "").strip() == "1"

    context = {
        "pool": pool,
        "participant": participant,
        "active_tab": active_tab,
        "show_reprocess_notice": show_reprocess_notice,
        **pool_context,
    }
    return render(request, "pool/detail.html", context)


@login_required
@require_http_methods(["POST"])
def join_pool(request, slug):
    pool = get_object_or_404(Pool, slug=slug, is_active=True)
    invite_token_value = (request.POST.get("invite_token") or "").strip()

    if not invite_token_value:
        messages.error(request, "Informe um token de convite para entrar neste bolão.")
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
        messages.error(request, "Informe um token de convite para entrar em um bolão.")
        return redirect("penninicup:index")

    try:
        UUID(invite_token_value)
    except (TypeError, ValueError):
        messages.error(request, "Token inválido ou sem bolão associado.")
        return redirect("penninicup:index")

    from src.accounts.models import InviteToken

    token_obj = InviteToken.objects.filter(token=invite_token_value).select_related("pool").first()
    if not token_obj or token_obj.pool_id is None:
        messages.error(request, "Token inválido ou sem bolão associado.")
        return redirect("penninicup:index")

    pool = token_obj.pool
    if not pool.is_active:
        messages.error(request, "O bolão deste token está inativo.")
        return redirect("penninicup:index")

    participant = _join_pool_with_token(request, pool, invite_token_value)
    if participant is None:
        return redirect("penninicup:index")

    return redirect("pool:detail", slug=pool.slug)


@login_required
@require_http_methods(["POST"])
@ratelimit(key="user_or_ip", rate="30/m", method="POST", block=False)
def save_bet(request, slug, match_id):
    pool = get_object_or_404(Pool, slug=slug, is_active=True)
    participant = get_object_or_404(PoolParticipant, pool=pool, user=request.user, is_active=True)
    match = get_object_or_404(Match.objects.select_related("stage"), id=match_id, season=pool.season)

    _ensure_participant_bets(participant=participant, matches=[match], can_bet=participant.can_bet())

    existing_bet = PoolBet.objects.filter(participant=participant, match=match).first()
    if existing_bet is None:
        existing_bet = PoolBet(participant=participant, match=match)

    form = PoolBetForm(request.POST, instance=existing_bet, match=match)
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"

    if getattr(request, "limited", False):
        if is_ajax:
            return JsonResponse({"ok": False, "error": "Muitas tentativas. Aguarde e tente novamente."}, status=429)
        messages.error(request, "Muitas tentativas de salvar palpites. Aguarde e tente novamente.")
        return redirect("pool:detail", slug=pool.slug)

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
        friendly_error = _friendly_save_bet_error(exc)
        if is_ajax:
            return JsonResponse({"ok": False, "error": friendly_error}, status=400)
        messages.error(request, friendly_error)
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
@ratelimit(key="user_or_ip", rate="30/m", method="POST", block=False)
def save_bets_bulk(request, slug):
    pool = get_object_or_404(Pool, slug=slug, is_active=True)
    participant = get_object_or_404(PoolParticipant, pool=pool, user=request.user, is_active=True)

    if getattr(request, "limited", False):
        messages.error(request, "Muitas tentativas de salvar palpites. Aguarde e tente novamente.")
        return redirect("pool:detail", slug=pool.slug)

    if not participant.can_bet():
        messages.error(request, "Participante sem permissão para palpitar.")
        return redirect("pool:detail", slug=pool.slug)

    matches = list(
        Match.objects.filter(season=pool.season)
        .select_related("stage")
        .order_by("match_number", "match_date_brasilia")
    )
    existing_bets = _ensure_participant_bets(
        participant=participant,
        matches=matches,
        can_bet=participant.can_bet(),
    )

    saved_count = 0
    saved_group_count = 0
    validation_errors = []

    top_scorer_changed = False
    top_scorer_candidate = participant.top_scorer_pred

    if not pool.is_phase_locked(PHASE_GROUP):
        top_scorer_value = (request.POST.get("top_scorer_pred") or "").strip()
        top_scorer_before = participant.top_scorer_pred_id
        if top_scorer_value:
            selected_player = _top_scorer_options_for_pool(pool).filter(id=top_scorer_value).first()
            if selected_player is None:
                validation_errors.append("Artilheiro inválido para esta temporada.")
            else:
                top_scorer_candidate = selected_player
        else:
            top_scorer_candidate = None

        if (top_scorer_candidate.id if top_scorer_candidate else None) != top_scorer_before:
            top_scorer_changed = True

    bets_to_save = []

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
                    validation_errors.append(f"Jogo {match.match_number}: {error}")
            continue

        bet_obj = form.save(commit=False)
        bet_obj.participant = participant
        bet_obj.match = match
        bets_to_save.append((bet_obj, before_state, phase, match.match_number))

    if validation_errors:
        for error in validation_errors:
            messages.error(request, error)
        messages.error(request, "Não foi possível salvar os palpites. Corrija os erros e tente novamente.")
        return redirect("pool:detail", slug=pool.slug)

    try:
        with transaction.atomic():
            if not pool.is_phase_locked(PHASE_GROUP) and top_scorer_changed:
                participant.top_scorer_pred = top_scorer_candidate
                participant.save(update_fields=["top_scorer_pred"])

            for bet_obj, before_state, phase, _ in bets_to_save:
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
        logger.exception(
            "Falha ao salvar lote de palpites: participant=%s error=%s",
            participant.id,
            str(exc),
        )
        messages.error(request, "Não foi possível salvar os palpites. Tente novamente.")
        return redirect("pool:detail", slug=pool.slug)

    if saved_group_count:
        enqueue_projection_recalc(participant)

    total_changes = saved_count + (1 if top_scorer_changed else 0)

    if not total_changes:
        messages.info(request, "Nenhuma alteração para salvar.")
    elif saved_group_count:
        messages.warning(
            request,
            "Palpites salvos. Revise os jogos de mata-mata: os classificados podem ter sido alterados.",
        )
    elif total_changes:
        messages.success(request, f"{total_changes} alteração(ões) salva(s) com sucesso.")

    return redirect("pool:detail", slug=pool.slug)
