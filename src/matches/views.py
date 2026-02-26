from django.shortcuts import render

from src.matches.models import GroupStage, Match


def _make_pairs(lst):
    """Agrupa uma lista em pares: [[m0, m1], [m2, m3], ...]"""
    return [lst[i : i + 2] for i in range(0, len(lst), 2)]


def match_list(request):
    """Página de resultados das partidas."""
    tab = request.GET.get("tab", "group")

    if tab == "knockout":
        stage_order = ["R32", "R16", "QF", "SF"]
        stage_labels = {
            "R32": "32 Avos",
            "R16": "Oitavas",
            "QF": "Quartas",
            "SF": "Semifinal",
        }

        all_ko = list(
            Match.objects.filter(stage__in=stage_order).select_related("home_team", "away_team").order_by("start_time")
        )

        used_stages = {m.stage for m in all_ko}
        active_stages = [s for s in stage_order if s in used_stages]

        bracket_left = []
        bracket_right = []

        for stage in active_stages:
            matches = [m for m in all_ko if m.stage == stage]
            n = len(matches)
            left = matches[: n // 2]
            right = matches[n // 2 :]
            bracket_left.append(
                {
                    "stage": stage,
                    "label": stage_labels[stage],
                    "pairs": _make_pairs(left),
                    "is_outermost": False,
                }
            )
            bracket_right.append(
                {
                    "stage": stage,
                    "label": stage_labels[stage],
                    "pairs": _make_pairs(right),
                    "is_outermost": False,
                }
            )

        # lado esquerdo: 1º elemento é o mais externo
        if bracket_left:
            bracket_left[0]["is_outermost"] = True

        # lado direito invertido: SF fica junto ao centro, R32 na extremidade
        bracket_right = list(reversed(bracket_right))
        if bracket_right:
            bracket_right[-1]["is_outermost"] = True

        # altura do container: maior n° de partidas num lado × 100 px
        max_matches_side = max(
            (sum(len(p) for p in r["pairs"]) for r in bracket_left),
            default=2,
        )
        bracket_height = max(max_matches_side * 100, 320)

        final_match = Match.objects.filter(stage="FINAL").select_related("home_team", "away_team").first()

        return render(
            request,
            "matches/match_list.html",
            {
                "bracket_left": bracket_left,
                "bracket_right": bracket_right,
                "final_match": final_match,
                "bracket_height": bracket_height,
                "groups": [],
                "active_tab": "knockout",
                "page_mode": "result",
            },
        )

    # ── Fase de Grupos ──────────────────────────────────────────────
    all_groups = GroupStage.objects.prefetch_related("entries__team").order_by("name")
    groups = []
    for group in all_groups:
        team_ids = list(group.entries.values_list("team_id", flat=True))
        matches = (
            Match.objects.filter(
                stage="GROUP",
                home_team_id__in=team_ids,
                away_team_id__in=team_ids,
            )
            .select_related("home_team", "away_team")
            .order_by("start_time")
        )
        groups.append({"group": group, "matches": matches})

    return render(
        request,
        "matches/match_list.html",
        {
            "groups": groups,
            "bracket_left": [],
            "bracket_right": [],
            "final_match": None,
            "bracket_height": 320,
            "active_tab": "group",
            "page_mode": "result",
        },
    )
