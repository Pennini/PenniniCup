import logging

from django.db.models import Prefetch
from django.shortcuts import redirect, render

from src.football.models import Group, Match, Standing

logger = logging.getLogger(__name__)

STAGE_GROUP = "GROUP"
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


def _make_pairs(items):
    """Agrupa uma lista em pares: [[m0, m1], [m2, m3], ...]."""
    return [items[index : index + 2] for index in range(0, len(items), 2)]


def _normalize_stage_key(stage):
    """Normaliza uma fase para uma chave canônica usada na renderização."""
    if not stage:
        return ""

    stage_name = (stage.name or "").upper().replace("-", " ").strip()

    if "GROUP" in stage_name or "GRUPO" in stage_name or "PRIMEIRA FASE" in stage_name:
        return STAGE_GROUP
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


def _resolve_tab_or_redirect(request):
    """Valida tab e retorna redirect para fallback quando necessário."""
    tab = request.GET.get("tab")
    if tab not in ("classification", "group", "knockout"):
        return None, redirect(f"{request.path}?tab=group")
    return tab, None


def _base_matches_queryset():
    return Match.objects.select_related("home_team", "away_team", "stage").order_by(
        "match_date_brasilia", "match_number"
    )


def _build_group_payload(all_matches):
    all_groups = Group.objects.prefetch_related(
        Prefetch(
            "matches",
            queryset=_base_matches_queryset(),
            to_attr="prefetched_matches",
        ),
        Prefetch(
            "standings",
            queryset=Standing.objects.select_related("team").order_by("position", "team__name"),
            to_attr="prefetched_standings",
        ),
    ).order_by("name")

    groups = []
    for group in all_groups:
        matches = [match for match in getattr(group, "prefetched_matches", [])]
        matches.sort(key=lambda m: (m.match_number, m.match_date_brasilia))
        standings = [standing for standing in getattr(group, "prefetched_standings", [])]
        groups.append({"group": group, "matches": matches, "standings": standings})

    return groups


def _build_knockout_payload(all_matches):
    grouped_matches = {stage_key: [] for stage_key in KNOCKOUT_STAGE_ORDER}
    final_match = None
    third_place_match = None

    for match in all_matches:
        stage_key = _normalize_stage_key(match.stage)
        if stage_key in grouped_matches:
            grouped_matches[stage_key].append(match)
        elif stage_key == STAGE_FINAL and final_match is None:
            final_match = match
        elif stage_key == STAGE_THIRD and third_place_match is None:
            third_place_match = match

    active_stages = [stage_key for stage_key in KNOCKOUT_STAGE_ORDER if grouped_matches[stage_key]]
    bracket_left = []
    bracket_right = []

    for stage_key in active_stages:
        stage_matches = grouped_matches[stage_key]
        half = len(stage_matches) // 2
        left_matches = stage_matches[:half]
        right_matches = stage_matches[half:]

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


def _build_match_page_context(tab):
    all_matches = list(_base_matches_queryset())
    knockout_data = _build_knockout_payload(all_matches)
    groups = _build_group_payload(all_matches)

    if tab == "classification":
        return {
            "groups": groups,
            "bracket_left": [],
            "bracket_right": [],
            "final_match": None,
            "third_place_match": None,
            "bracket_height": 320,
            "active_tab": "classification",
            "page_mode": "result",
        }

    if tab == "knockout":
        return {
            "groups": [],
            "active_tab": "knockout",
            "page_mode": "result",
            **knockout_data,
        }

    return {
        "groups": groups,
        "bracket_left": [],
        "bracket_right": [],
        "final_match": None,
        "third_place_match": None,
        "bracket_height": 320,
        "active_tab": "group",
        "page_mode": "result",
    }


def match_list(request):
    """Página de resultados das partidas."""
    tab, redirect_response = _resolve_tab_or_redirect(request)
    if redirect_response:
        return redirect_response

    context = _build_match_page_context(tab=tab)
    return render(request, "football/match_list.html", context)
