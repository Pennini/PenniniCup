# Profile — Group-stage audit + knockout color feedback

**Date:** 2026-05-24
**Branch:** feature/redesign-scoring-rules
**Files touched (planned):**

- `src/pool/services/ranking.py` — expand qualifier bonus to top 3
- `src/penninicup/views.py` — knockout `predicted_winners` enrichment + new `_build_group_audit`
- `src/penninicup/templates/penninicup/profile.html` — color tokens + audit panel
- `src/penninicup/templates/penninicup/rules.html` — wording update
- `src/pool/tests.py` (or new module) — tests for new bonus behavior + audit builder

## Goal

Give the profile page two pieces of post-match feedback so the user can audit the bonus points received:

1. **Knockout phase summary** — color predicted classified teams green if they advanced in that phase, red if eliminated, neutral while phase has no real results yet.
1. **Group-stage audit panel** — per group, show predicted vs real top 3 side-by-side, color predicted teams by qualification, badge per-team points so the displayed total reconciles with the `qualifier_bonus_points` stored on the participant.

The second item also expands the scoring rule: 3rd place now counts for the qualifier bonus with the same logic as 1st/2nd (driven by the 48-team / best-third-placed advancement format).

## Non-goals

- No change to knockout scoring (`team_advancement_bonus`, exact-score points, etc.).
- No new model fields or migrations.
- No snapshot tests of the template.
- No change to the projection logic that fills `participant.projected_standings` — it already stores positions 1..N per group; the audit just reads positions 1..3.

## Architecture

All data is built in the view layer. Template stays free of Python-side computation (it only iterates pre-built structures). Rationale: the template already nests `{% if %}` blocks four levels deep and runs inline JS for the date/group toggle; injecting more logic there would be brittle and would risk N+1 queries during render.

Two new structures are added to the profile context:

- `predicted_winners` (existing field on each phase dict) becomes a list of `{team, advanced, decided}` instead of a list of `Team`. `real_winners` keeps its current shape (list of `Team`).
- `group_audit` (new top-level key): `list[{group, rows[3], group_points, has_real}]`.

## Scoring change (top 2 → top 3)

### Code change

`src/pool/services/ranking.py`, `_calculate_group_qualifier_bonus`:

```python
real_qualifiers_by_group = {}
for s in Standing.objects.filter(season=season, position__lte=3).values("group_id", "position", "team_id"):
    real_qualifiers_by_group.setdefault(s["group_id"], {})[s["position"]] = s["team_id"]

if not real_qualifiers_by_group:
    return 0

proj_top3 = {}
for s in participant.projected_standings.filter(position__lte=3).values("group_id", "position", "team_id"):
    proj_top3.setdefault(s["group_id"], {})[s["position"]] = s["team_id"]

total = 0
for group_id, real_positions in real_qualifiers_by_group.items():
    proj_positions = proj_top3.get(group_id, {})
    real_qualifiers = set(real_positions.values())
    for position, team_id in proj_positions.items():
        if team_id in real_qualifiers:
            total += scoring_config.group_qualifier_points
            if real_positions.get(position) == team_id:
                total += scoring_config.group_qualifier_position_bonus

return total
```

Net diff: `position__lte=2` → `position__lte=3` in both queries; rename local `real_top2`/`proj_top2` → `real_qualifiers_by_group`/`proj_top3` for clarity. Loop body unchanged.

### Rules page update

`src/penninicup/templates/penninicup/rules.html`:

- Line 165: "Para cada time que você acertou como classificado da fase de grupos" → "...como classificado (1º, 2º ou 3º) da fase de grupos".
- Line 171: "+ posição exata (1º ou 2º)" → "+ posição exata (1º, 2º ou 3º)".
- Lines 178-179 examples: keep, but add a third example covering a 3rd-place hit.

### Backfill

After deploy, existing participants will need their `qualifier_bonus_points` and `total_points` recomputed. Plan: run `recalculate_participant_scores` for every `PoolParticipant` (one-off mgmt command if none exists; otherwise reuse). Implementation step verifies whether a suitable command already exists before adding a new one.

## Knockout color feedback

### View change

`_build_knockout_by_phase` in `src/penninicup/views.py`:

```python
real_winners = [r["match"].winner for r in rows if r["match"].winner]
real_winners_ids = {t.id for t in real_winners}
decided = bool(real_winners_ids)
predicted = []
for r in rows:
    bet = r.get("bet")
    if not (bet and bet.winner_pred):
        continue
    predicted.append(
        {
            "team": bet.winner_pred,
            "advanced": bet.winner_pred_id in real_winners_ids,
            "decided": decided,
        }
    )
```

`real_winners` keeps its current shape (list of `Team`) — only `predicted_winners` changes.

### Template change

`src/penninicup/templates/penninicup/profile.html`, lines 348-354 (the `{% for team in phase.predicted_winners %}` loop):

```django
{% for item in phase.predicted_winners %}
<span class="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs
    {% if not item.decided %}border-orange-500/20 bg-orange-500/5 text-orange-300
    {% elif item.advanced %}border-green-500/30 bg-green-500/10 text-green-300
    {% else %}border-red-500/30 bg-red-500/10 text-red-300{% endif %}">
    {% if item.team.flag_image_url %}<span class="inline-flex h-3.5 w-3.5 overflow-hidden rounded-full ring-1 ring-neutral-600 bg-neutral-800 shrink-0"><img src="{{ item.team.flag_image_url }}" alt="" class="h-full w-full object-cover" /></span>{% endif %}
    {{ item.team.name }}
</span>
{% endfor %}
```

## Group-stage audit panel

### View — `_build_group_audit`

New function in `src/penninicup/views.py`, called only when `selected_pool`, `can_view_predictions`, and `selected_participant` are all truthy:

```python
def _build_group_audit(participant, season, scoring_config):
    from src.football.models import Group, Standing

    real_rows = (
        Standing.objects.filter(season=season, position__lte=3)
        .select_related("team", "group")
        .order_by("group__name", "position")
    )

    proj_rows = (
        participant.projected_standings.filter(position__lte=3)
        .select_related("team", "group")
        .order_by("group__name", "position")
    )

    real_by_group = defaultdict(dict)  # {group_id: {position: Standing}}
    real_qualifiers_by_group = defaultdict(set)  # {group_id: {team_id, ...}}
    for s in real_rows:
        real_by_group[s.group_id][s.position] = s
        real_qualifiers_by_group[s.group_id].add(s.team_id)

    proj_by_group = defaultdict(dict)  # {group_id: {position: ProjectedStanding}}
    for p in proj_rows:
        proj_by_group[p.group_id][p.position] = p

    audit = []
    for group in Group.objects.filter(stage__season=season).order_by("name"):
        real_positions = real_by_group.get(group.id, {})
        proj_positions = proj_by_group.get(group.id, {})
        has_real = bool(real_positions)
        real_qualifier_ids = real_qualifiers_by_group.get(group.id, set())

        rows = []
        group_points = 0
        for position in (1, 2, 3):
            proj = proj_positions.get(position)
            real = real_positions.get(position)
            predicted_team = proj.team if proj else None
            real_team = real.team if real else None

            qualified = bool(has_real and predicted_team and predicted_team.id in real_qualifier_ids)
            position_match = bool(qualified and real_team and predicted_team.id == real_team.id)

            points = 0
            if qualified:
                points = scoring_config.group_qualifier_points
                if position_match:
                    points += scoring_config.group_qualifier_position_bonus

            group_points += points
            rows.append(
                {
                    "position": position,
                    "predicted_team": predicted_team,
                    "real_team": real_team,
                    "qualified": qualified,
                    "position_match": position_match,
                    "points": points,
                }
            )

        audit.append(
            {
                "group": group,
                "rows": rows,
                "group_points": group_points,
                "has_real": has_real,
            }
        )

    return audit
```

Wire-up: in `src/penninicup/views.py`, alongside the existing `_build_knockout_by_phase` call (around line 196 of the profile view), call `_build_group_audit(selected_participant, selected_pool.season, scoring_config)` and add `"group_audit": audit` to the context dict that is merged with `predictions_context`. Skip when `scoring_config is None`, `selected_participant is None`, or `not can_view_predictions`.

Sanity check: sum of `group_points` across all groups must equal `selected_participant.qualifier_bonus_points` after the scoring change is deployed and `recalculate_participant_scores` has run. This is the audit invariant.

### Template — replace lines 261-271

The current `qualifier_bonus_points` article (small green strip) is replaced with a wider article that contains the same header on top and the audit grid below:

```django
{% if group_audit %}
<article class="rounded-xl border border-emerald-700/30 bg-emerald-900/10 p-4 space-y-4">
    <div class="flex items-center justify-between gap-3">
        <div>
            <p class="text-xs uppercase tracking-wide text-emerald-400">Bônus de Classificados de Grupos</p>
            <p class="text-sm text-neutral-300 mt-0.5">Pontos por times acertados na classificação (1º, 2º, 3º) de cada grupo</p>
        </div>
        {% if selected_participant.qualifier_bonus_points %}
        <span class="text-xl font-bold text-emerald-300 shrink-0">+{{ selected_participant.qualifier_bonus_points }} pts</span>
        {% endif %}
    </div>

    <div class="grid gap-3 sm:grid-cols-2">
        {% for entry in group_audit %}
        <div class="rounded-lg border border-neutral-700/60 bg-neutral-900/60 p-3 space-y-2">
            <div class="flex items-center justify-between gap-2">
                <span class="text-sm font-semibold text-neutral-100">Grupo {{ entry.group.name }}</span>
                {% if entry.group_points %}
                <span class="text-xs font-semibold text-emerald-300">+{{ entry.group_points }} pts</span>
                {% elif entry.has_real %}
                <span class="text-xs text-neutral-500">0 pts</span>
                {% endif %}
            </div>

            <div class="grid grid-cols-2 gap-2 text-xs">
                <div class="space-y-1">
                    <p class="text-[10px] uppercase tracking-wide text-neutral-500">Meu palpite</p>
                    {% for row in entry.rows %}
                    <div class="flex items-center gap-1.5 rounded border px-1.5 py-0.5
                        {% if not entry.has_real or not row.predicted_team %}border-neutral-700 bg-neutral-800/40 text-neutral-300
                        {% elif row.qualified %}border-green-500/30 bg-green-500/10 text-green-300
                        {% else %}border-red-500/30 bg-red-500/10 text-red-300{% endif %}
                        {% if row.position_match %}ring-1 ring-emerald-400/60{% endif %}">
                        <span class="text-[10px] font-bold opacity-70 shrink-0">{{ row.position }}º</span>
                        {% if row.predicted_team %}
                        {% if row.predicted_team.flag_image_url %}<span class="inline-flex h-3.5 w-3.5 overflow-hidden rounded-full ring-1 ring-neutral-700 bg-neutral-900 shrink-0"><img src="{{ row.predicted_team.flag_image_url }}" alt="" class="h-full w-full object-cover" /></span>{% endif %}
                        <span class="truncate">{{ row.predicted_team.name }}</span>
                        {% if row.points %}<span class="ml-auto text-[10px] font-semibold text-emerald-300 shrink-0">+{{ row.points }}</span>{% endif %}
                        {% else %}
                        <span class="text-neutral-600 italic">—</span>
                        {% endif %}
                    </div>
                    {% endfor %}
                </div>
                <div class="space-y-1">
                    <p class="text-[10px] uppercase tracking-wide text-neutral-500">Real</p>
                    {% for row in entry.rows %}
                    <div class="flex items-center gap-1.5 rounded border border-neutral-700 bg-neutral-800/40 px-1.5 py-0.5 text-neutral-200">
                        <span class="text-[10px] font-bold opacity-70 shrink-0">{{ row.position }}º</span>
                        {% if row.real_team %}
                        {% if row.real_team.flag_image_url %}<span class="inline-flex h-3.5 w-3.5 overflow-hidden rounded-full ring-1 ring-neutral-700 bg-neutral-900 shrink-0"><img src="{{ row.real_team.flag_image_url }}" alt="" class="h-full w-full object-cover" /></span>{% endif %}
                        <span class="truncate">{{ row.real_team.name }}</span>
                        {% else %}
                        <span class="text-neutral-600 italic">pendente</span>
                        {% endif %}
                    </div>
                    {% endfor %}
                </div>
            </div>
        </div>
        {% endfor %}
    </div>
</article>
{% endif %}
```

Old `{% if selected_participant and selected_participant.qualifier_bonus_points %} … {% endif %}` block (lines 261-271) is removed — the new audit article subsumes it. The new article shows even when `qualifier_bonus_points == 0`, as long as `group_audit` is non-empty, so the user can see what they got wrong.

Mobile: `sm:grid-cols-2` collapses to single column; each group card still has a 2-column inner grid (palpite | real) — acceptable at narrow widths because team names truncate.

## Edge cases

| Case                                                            | Behavior                                                                            |
| --------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| No `Standing` rows yet for a group                              | `has_real=False`, predicted shown in neutral grey, real column shows "pendente"     |
| Participant has no `projected_standings` for group (no bet)     | `predicted_team=None`, row shows "—"                                                |
| `selected_pool is None` or `can_view_predictions=False`         | `group_audit` not added to context                                                  |
| Pool has no `scoring_config`                                    | `_build_group_audit` returns `[]` (skip the panel)                                  |
| `Standing` has only 2 positions filled                          | 3rd row neutral, 1st/2nd colored as usual                                           |
| Predicted team in 3rd slot but real top-3 contains it in 1st    | `qualified=True`, `position_match=False` → green, no ring, `+qualifier_points` only |
| Predicted team appears multiple times in proj (data corruption) | Out of scope — relies on existing uniqueness constraint on `projected_standings`    |

## Testing

`src/pool/tests.py` (extend existing file or add `test_qualifier_bonus_top3.py` next to it):

**Scoring rule (`_calculate_group_qualifier_bonus`):**

1. Real qualifiers = top 3. Predicted top 3 matches exactly → `3 * (qualifier_points + position_bonus)`.
1. Predicted 3rd-place team that finishes 3rd in real → `qualifier_points + position_bonus`.
1. Predicted 3rd-place team that finishes 1st in real → `qualifier_points` (no position match).
1. Predicted 3rd-place team that finishes 4th in real → `0`.
1. Predicted 1st-place team that finishes 3rd in real → `qualifier_points` (no position match).
1. Empty `Standing` → returns 0 (no change in behavior).

**Audit builder (`_build_group_audit`):**
7\. Returns one entry per group ordered by name.
8\. `group_points` for each entry equals what `_calculate_group_qualifier_bonus` would award for that group alone.
9\. `has_real=False` when group has no `Standing` rows.
10\. Sum of `group_points` across all audit entries equals total `qualifier_bonus_points` after `recalculate_participant_scores`. (Integration test, single participant + fixture.)

No template snapshot tests.

## Rollout order

1. Land scoring change (`ranking.py` + tests 1-6).
1. Run backfill: `recalculate_participant_scores` over every `PoolParticipant`.
1. Land view change (`predicted_winners` enrichment + `_build_group_audit`) and template change in one PR.
1. Update `rules.html` wording in the same PR as step 3.

Steps 1-2 are independent of UI and can ship first if needed. Splitting reduces blast radius of the scoring change.

## Open questions

None at design time. The implementation step verifies:

- Whether a mgmt command for full ranking recalc already exists (`grep -r "recalculate" src/pool/management`).
- Whether `Standing.position` is 1-indexed (assumed by the queries above — match existing usage in `_calculate_group_qualifier_bonus`).
