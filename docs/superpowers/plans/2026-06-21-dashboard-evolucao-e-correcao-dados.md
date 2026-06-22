# Dashboard de visão geral: evolução single-user + correção dos dados — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trocar o gráfico de evolução por um seletor de um participante por vez, corrigir o histórico de ranking (as-of) e o gap de gatilho do sync, servir KPIs/aproveitamento ao vivo, e corrigir o Hall da Fama (Pegando Fogo + rótulos).

**Architecture:** Monólito Django. A dashboard de visão geral serve um payload pool-wide cacheado (`PoolDashboardSnapshot`: hall + `evolution_all` + token de versão) e sobrepõe ao vivo, por request, os dados baratos por participante (posição, gap, aproveitamento, participante selecionado) a partir de `build_pool_leaderboard` — a mesma fonte da página de ranking. O histórico (`PoolRankingHistory`) passa a ser sempre reconstruído pelo motor as-of correto (`backfill_pool_history`), disparado tanto pelo worker (sinal por jogo) quanto pelo chokepoint de recálculo por bolão (sync).

**Tech Stack:** Python 3.12, Django 6, PostgreSQL, Chart.js (UMD vendored), TailwindCSS. Filas DB-backed (sem Celery) processadas por `run_projection_worker` (poll 1s).

**Spec:** `docs/superpowers/specs/2026-06-21-dashboard-evolucao-e-correcao-dados-design.md`

## Global Constraints

- **Comando de teste (Git Bash):** `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test <caminho.pontilhado> --verbosity=2`
  - PowerShell: `$env:PENNINICUP_SETTINGS_PROFILE='test'; poetry run python -m src.manage test <caminho.pontilhado> --verbosity=2`
- **Nunca** usar `datetime.now()` — sempre `django.utils.timezone.now()`. `USE_TZ=True`, tudo aware, timezone `America/Sao_Paulo`.
- `bulk_create`/`bulk_update` **não** disparam `post_save` — não confiar em sinais para esses caminhos.
- Ruff: target py312, line-length 119. Imports ordenados (isort). Rodar `make lint` antes de cada commit final de tarefa quando tocar Python.
- Frontend sem build extra: `dashboard.js` é JS puro vanilla (ES5-ish, sem libs novas); nomes de usuário sempre via `textContent` (nunca `innerHTML`).
- `docs/` é gitignored: commitar specs/planos com `git add -f`. O hook **mdformat** pode reformatar markdown e abortar o commit — re-staging e commit de novo.
- Mensagens de commit terminam com:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

______________________________________________________________________

## File Structure

**Backend (modificar):**

- `src/rankings/services/position_snapshot.py` — `snapshot_round_for_match` passa a delegar ao motor as-of (Task 1).
- `src/rankings/services/derived.py` — **novo**: `refresh_pool_derived_data(pool)` (Task 2).
- `src/pool/services/ranking.py` — `recalculate_all_pools` chama o refresh por bolão (Task 2).
- `src/pool/management/commands/recalculate_pool_scores.py` — branch de um bolão também faz refresh (Task 2).
- `src/rankings/services/dashboard.py` — payload `evolution_all`, overlay ao vivo (KPIs/aproveitamento/evolução), token de versão + guard de leitura, `_longest_streak` corrigido (Tasks 3, 4, 5, 6).
- `src/rankings/static/rankings/dashboard.js` — evolução single-user + seletor; rótulos do Hall (Tasks 3, 6).
- `src/rankings/templates/rankings/dashboard_overview.html` — `<select>` no card de evolução (Task 3).
- `src/rankings/management/commands/rebuild_dashboard_snapshots.py` — **novo**: reparo único (Task 7).
- `src/rankings/management/commands/diagnose_dashboard.py` — tolerar novo formato do payload (Task 8).

**Testes (modificar):** `src/rankings/tests.py` — atualizar testes existentes que mudam de forma e adicionar os novos.

______________________________________________________________________

## Task 1: Seção B — snapshot de histórico as-of

Unifica o caminho ao vivo no motor correto: `snapshot_round_for_match` deixa de carimbar agregados atuais e passa a chamar `backfill_pool_history(pool)` por bolão afetado.

**Files:**

- Modify: `src/rankings/services/position_snapshot.py` (substituir todo o corpo de cálculo)
- Test: `src/rankings/tests.py` (classe nova `SnapshotAsOfTest`)

**Interfaces:**

- Consumes: `backfill_pool_history(pool)` de `src/rankings/services/history_backfill.py` (retorna `int` nº de rodadas; idempotente; `@transaction.atomic`).

- Produces: `snapshot_round_for_match(match) -> list[Pool]` (os bolões afetados; assinatura preservada para `snapshot_queue.process_next_ranking_snapshot_job`, que continua enfileirando o dashboard por bolão).

- [ ] **Step 1: Escrever o teste que falha**

Em `src/rankings/tests.py`, adicionar (usa o helper existente `_build_pool_with_3_rounds` e o import já presente `snapshot_round_for_match`):

```python
class SnapshotAsOfTest(TestCase):
    def setUp(self):
        self.pool, self.participants, self.matches = _build_pool_with_3_rounds()

    def test_snapshot_round_matches_backfill_even_out_of_order(self):
        # Processa os jogos FORA de ordem cronológica (3, 1, 2): o caminho antigo
        # carimbava agregados atuais e corrompia o round_index/posição as-of.
        for match in [self.matches[2], self.matches[0], self.matches[1]]:
            snapshot_round_for_match(match)

        from_signal = {
            (h.participant_id, h.round_index): h.position for h in PoolRankingHistory.objects.filter(pool=self.pool)
        }

        # Verdade as-of independente da ordem de processamento.
        backfill_pool_history(self.pool)
        as_of = {
            (h.participant_id, h.round_index): h.position for h in PoolRankingHistory.objects.filter(pool=self.pool)
        }
        self.assertEqual(from_signal, as_of)

    def test_snapshot_returns_affected_pools(self):
        affected = snapshot_round_for_match(self.matches[0])
        self.assertIn(self.pool, affected)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests.SnapshotAsOfTest --verbosity=2`
Expected: FAIL em `test_snapshot_round_matches_backfill_even_out_of_order` (posições/round_index divergem — bug do carimbo de agregados).

- [ ] **Step 3: Reescrever `snapshot_round_for_match`**

Substituir todo o conteúdo de `src/rankings/services/position_snapshot.py` por:

```python
from src.pool.models import Pool
from src.rankings.services.history_backfill import backfill_pool_history


def snapshot_round_for_match(match):
    """Reconstrói o histórico de ranking dos bolões afetados por um jogo encerrado.

    Antes este caminho carimbava os agregados *atuais* da temporada em cada rodada,
    o que corrompia o histórico em correções de placar, bônus de fim de torneio e
    processamento fora de ordem. Agora delega ao motor as-of (`backfill_pool_history`),
    idempotente e cronológico — um único caminho correto. Retorna os bolões afetados
    para a fila enfileirar o rebuild da dashboard de cada um.
    """
    if match.home_score is None or match.away_score is None:
        return []

    affected_pools = list(
        Pool.objects.filter(
            season=match.season,
            is_active=True,
            participants__bets__match=match,
        ).distinct()
    )

    for pool in affected_pools:
        backfill_pool_history(pool)

    return affected_pools
```

- [ ] **Step 4: Rodar e ver passar**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests.SnapshotAsOfTest --verbosity=2`
Expected: PASS (2 testes).

- [ ] **Step 5: Regressão do módulo de rankings**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests --verbosity=2`
Expected: PASS. Se algum teste antigo dependia do carimbo de agregados em `snapshot_round_for_match`, ajustá-lo para a verdade as-of. (`make lint` se mexeu em imports.)

- [ ] **Step 6: Commit**

```bash
git add src/rankings/services/position_snapshot.py src/rankings/tests.py
git commit -m "$(cat <<'EOF'
fix(rankings): snapshot de histórico delega ao motor as-of (Seção B)

snapshot_round_for_match parava de carimbar agregados atuais (corrompia em
correção de placar/bônus/fora de ordem); agora chama backfill_pool_history
por bolão afetado. Caminho único e correto.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

______________________________________________________________________

## Task 2: Seção C2 — refresh de derivados no chokepoint do recálculo

Fecha o gap do sync (`bulk_create` não dispara sinal): centraliza o refresh de dados derivados (histórico as-of + enfileirar dashboard) num ponto único por bolão, usado pelo laço de `recalculate_all_pools` e pelo comando de recálculo de um bolão.

**Files:**

- Create: `src/rankings/services/derived.py`
- Modify: `src/pool/services/ranking.py:313-319` (`recalculate_all_pools`)
- Modify: `src/pool/management/commands/recalculate_pool_scores.py:20-29`
- Test: `src/rankings/tests.py` (classe nova `RefreshDerivedDataTest`)

**Interfaces:**

- Consumes: `backfill_pool_history(pool)`; `enqueue_dashboard_snapshot(pool)` de `src/rankings/services/dashboard_queue.py`.

- Produces: `refresh_pool_derived_data(pool) -> None` — reconstrói o histórico as-of e **depois** enfileira o rebuild da dashboard (ordem histórico-antes-do-dashboard).

- [ ] **Step 1: Escrever o teste que falha**

Em `src/rankings/tests.py`:

```python
class RefreshDerivedDataTest(TestCase):
    def setUp(self):
        self.pool, self.participants, self.matches = _build_pool_with_3_rounds()

    def test_recalculate_all_pools_rebuilds_history_and_enqueues_dashboard(self):
        from src.pool.services.ranking import recalculate_all_pools

        # Simula o sync: nenhum sinal, histórico/dashboard ainda vazios.
        PoolRankingHistory.objects.filter(pool=self.pool).delete()
        self.assertFalse(PoolDashboardSnapshotJob.objects.filter(pool=self.pool).exists())

        recalculate_all_pools(season=self.pool.season)

        # Histórico as-of reconstruído (3 rodadas x 3 participantes).
        self.assertEqual(
            PoolRankingHistory.objects.filter(pool=self.pool).count(),
            3 * len(self.participants),
        )
        # Rebuild da dashboard enfileirado para o bolão.
        self.assertTrue(PoolDashboardSnapshotJob.objects.filter(pool=self.pool).exists())
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests.RefreshDerivedDataTest --verbosity=2`
Expected: FAIL (nenhum job de dashboard enfileirado; histórico não reconstruído pelo recalc).

- [ ] **Step 3: Criar `refresh_pool_derived_data`**

Criar `src/rankings/services/derived.py`:

```python
from src.rankings.services.dashboard_queue import enqueue_dashboard_snapshot
from src.rankings.services.history_backfill import backfill_pool_history


def refresh_pool_derived_data(pool):
    """Atualiza os dados derivados de um bolão após os agregados mudarem.

    Ponto de entrada único para o caminho de recálculo (sync e comandos), onde o
    `bulk_create` de placares não dispara `post_save`. Reconstrói o histórico
    as-of e *depois* enfileira o rebuild do payload pesado da dashboard — nessa
    ordem, pois a dashboard lê `PoolRankingHistory`.
    """
    backfill_pool_history(pool)
    enqueue_dashboard_snapshot(pool)
```

- [ ] **Step 4: Chamar no laço de `recalculate_all_pools`**

Em `src/pool/services/ranking.py`, substituir o corpo de `recalculate_all_pools` (linhas 313-319):

```python
def recalculate_all_pools(season=None):
    from src.rankings.services.derived import refresh_pool_derived_data

    pools = Pool.objects.filter(is_active=True)
    if season is not None:
        pools = pools.filter(season=season)

    for pool in pools:
        recalculate_pool_scores(pool)
        refresh_pool_derived_data(pool)
```

(Import dentro da função: evita ciclo `pool` ↔ `rankings` no carregamento dos módulos.)

- [ ] **Step 5: Cobrir o branch de um bolão no comando**

Em `src/pool/management/commands/recalculate_pool_scores.py`, no branch de um único bolão (após `recalculate_pool_scores(pool)`, ~linha 24):

```python
            recalculate_pool_scores(pool)
            from src.rankings.services.derived import refresh_pool_derived_data
            refresh_pool_derived_data(pool)
```

(O branch "todos" chama `recalculate_all_pools`, já coberto pelo Step 4.)

- [ ] **Step 6: Rodar e ver passar**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests.RefreshDerivedDataTest --verbosity=2`
Expected: PASS.

- [ ] **Step 7: Regressão de pool + football + rankings**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests src.football.tests src.rankings.tests --verbosity=2`
Expected: PASS. (`make lint` se necessário.)

- [ ] **Step 8: Commit**

```bash
git add src/rankings/services/derived.py src/pool/services/ranking.py src/pool/management/commands/recalculate_pool_scores.py src/rankings/tests.py
git commit -m "$(cat <<'EOF'
fix(rankings): refresh de derivados no chokepoint de recálculo (Seção C2)

bulk_create do sync não dispara post_save; recalculate_all_pools agora
reconstrói o histórico as-of e enfileira o rebuild da dashboard por bolão
via refresh_pool_derived_data. Fecha o gap de gatilho do sync.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

______________________________________________________________________

## Task 3: Seção A — gráfico de evolução single-user + seletor

Troca o espaguete por uma linha só. O payload cacheado passa a guardar `evolution_all` (todos os elegíveis com histórico); o overlay anexa `current_participant_id`; o frontend ganha um `<select>` que troca a série em memória, sem novo fetch.

**Files:**

- Modify: `src/rankings/services/dashboard.py` (payload + overlay de evolução; remover gating top-N)
- Modify: `src/rankings/templates/rankings/dashboard_overview.html:93-103` (card de evolução)
- Modify: `src/rankings/static/rankings/dashboard.js:123-187` (`renderEvolution`)
- Test: `src/rankings/tests.py` (atualizar `DashboardServiceTest`)

**Interfaces:**

- Consumes: `_series_for_ids(pool, ids, username_by_id) -> list[{participant_id, label, points:[{round,position,points}]}]` (já existe).

- Produces:

  - Payload pool-wide: chave `evolution_all` (lista, ordem do leaderboard) substitui `evolution_series` + `selected_ids`.
  - Overlay: `data["evolution"] == {"all": <evolution_all>, "current_participant_id": <int>}`.

- [ ] **Step 1: Atualizar os testes (forma nova) — devem falhar**

Em `src/rankings/tests.py`, **substituir** `test_evolution_top5_plus_user_no_duplicates` (linha ~1464) por:

```python
    def test_evolution_all_contains_every_participant_with_history(self):
        evolution = build_dashboard_data(pool=self.pool, participant=self.p1)["evolution"]
        ids = {s["participant_id"] for s in evolution["all"]}
        self.assertEqual(ids, {self.p1.id, self.p2.id, self.p3.id})
        self.assertTrue(all(len(s["points"]) == 2 for s in evolution["all"]))
        self.assertEqual(evolution["current_participant_id"], self.p1.id)
```

Em `test_empty_pool_returns_safe_states` (linha ~1532) trocar:

```python
        self.assertEqual(data["evolution"]["all"], [])
```

(remover a linha antiga `self.assertEqual(data["evolution"]["series"], [])`).

- [ ] **Step 2: Rodar e ver falhar**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests.DashboardServiceTest --verbosity=2`
Expected: FAIL (`KeyError: 'all'` / payload ainda usa `series`).

- [ ] **Step 3: Backend — payload `evolution_all` + overlay**

Em `src/rankings/services/dashboard.py`:

(a) Remover a constante de gating no topo (linha ~26): apagar `EVOLUTION_TOP_N = 10` (manter `UTILIZATION_TOP_N = 10`).

(b) Em `build_dashboard_pool_payload`, remover `selected_ids` e trocar a montagem da evolução. O `return` passa a conter `evolution_all` no lugar de `selected_ids`/`evolution_series` (demais chaves serão enxugadas na Task 4):

```python
    leaderboard = build_pool_leaderboard(pool)
    username_by_id = {row.participant.id: row.participant.user.username for row in leaderboard}
    eligible_ids = list(username_by_id)  # ordem do leaderboard (posição atual)

    finished_matches = _finished_matches(pool.season)
    finished_ids = [match.id for match in finished_matches]
    max_points_by_id, denominator = _utilization_inputs(pool, finished_matches)

    return {
        "leader_points": leaderboard[0].participant.total_points if leaderboard else 0,
        "denominator": denominator,
        "positions": {row.participant.id: row.position for row in leaderboard},
        "username_by_id": username_by_id,
        "max_points_by_id": max_points_by_id,
        "evolution_all": _series_for_ids(pool, eligible_ids, username_by_id),
        "utilization_rows": _utilization_rows(leaderboard, max_points_by_id, denominator),
        "hall_of_fame": _hall_of_fame(pool, eligible_ids, username_by_id, leaderboard, finished_ids),
    }
```

(c) Remover a função `_evolution_series` (linhas ~228-230).

(d) Substituir `_evolution_overlay` (linhas ~233-244) por:

```python
def _evolution_overlay(payload, participant):
    """Séries já completas no cache; o overlay só marca a seleção padrão."""
    return {
        "all": payload.get("evolution_all", []),
        "current_participant_id": participant.id,
    }
```

(e) Em `_overlay_participant` (linha ~99), trocar a linha de evolução:

```python
        "evolution": _evolution_overlay(payload, participant),
```

- [ ] **Step 4: Rodar e ver passar (backend)**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests.DashboardServiceTest --verbosity=2`
Expected: PASS nos testes de evolução. (Outros testes da classe que dependem de `evolution_series` no payload são corrigidos na Task 4/regressão.)

- [ ] **Step 5: Template — `<select>` no card de evolução**

Em `src/rankings/templates/rankings/dashboard_overview.html`, substituir o `<h2>` do card de evolução (linha ~94) por um cabeçalho com o seletor:

```html
            <div class="flex flex-wrap items-center justify-between gap-3">
                <h2 class="text-sm font-semibold uppercase tracking-wide text-neutral-400">Evolução do participante</h2>
                <select data-evolution-select aria-label="Escolher participante"
                        class="rounded-md border border-neutral-700 bg-neutral-900 px-2 py-1 text-sm text-neutral-200"></select>
            </div>
```

- [ ] **Step 6: Frontend — `renderEvolution` single-user**

Em `src/rankings/static/rankings/dashboard.js`, substituir a função `renderEvolution` inteira (linhas ~123-187) por:

```javascript
    var evolutionChart = null;

    function buildEvolutionConfig(serie) {
        return {
            type: "line",
            data: {
                datasets: [
                    {
                        label: serie.label,
                        data: serie.points.map(function (point) {
                            return { x: point.round, y: point.position, pts: point.points };
                        }),
                        borderColor: USER_COLOR,
                        backgroundColor: USER_COLOR,
                        borderWidth: 4,
                        pointRadius: 3,
                        pointHoverRadius: 5,
                        tension: 0.45,
                        cubicInterpolationMode: "monotone",
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: "nearest", intersect: false },
                scales: {
                    x: {
                        type: "linear",
                        title: { display: true, text: "Rodada", color: TICK_COLOR },
                        ticks: { precision: 0, color: TICK_COLOR },
                        grid: { color: GRID_COLOR },
                    },
                    y: {
                        reverse: true,
                        min: 1,
                        title: { display: true, text: "Posição", color: TICK_COLOR },
                        ticks: { precision: 0, color: TICK_COLOR },
                        grid: { color: GRID_COLOR },
                    },
                },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: function (ctx) {
                                var raw = ctx.raw || {};
                                return ctx.dataset.label + ": #" + raw.y + " (" + raw.pts + " pts)";
                            },
                        },
                    },
                },
            },
        };
    }

    function renderEvolution(data) {
        var el = card("evolution");
        var evo = data.evolution || {};
        var all = evo.all || [];
        var select = el.querySelector("[data-evolution-select]");
        if (!all.length) {
            setState(el, "empty");
            return;
        }
        setState(el, "content");

        select.textContent = "";
        all.forEach(function (serie) {
            var opt = document.createElement("option");
            opt.value = String(serie.participant_id);
            opt.textContent = serie.label;
            select.appendChild(opt);
        });

        var current = evo.current_participant_id;
        var hasCurrent = all.some(function (serie) {
            return String(serie.participant_id) === String(current);
        });
        select.value = String(hasCurrent ? current : all[0].participant_id);

        function draw() {
            var serie = all.find(function (s) {
                return String(s.participant_id) === String(select.value);
            });
            if (!serie || typeof window.Chart !== "function") {
                return;
            }
            if (evolutionChart) {
                evolutionChart.destroy();
            }
            try {
                evolutionChart = new window.Chart(
                    document.getElementById("chart-evolution"),
                    buildEvolutionConfig(serie)
                );
            } catch (err) {
                console.error("dashboard: chart 'chart-evolution' failed", err);
            }
        }

        select.onchange = draw;
        draw();
    }
```

(Observação: este `renderEvolution` cria o `Chart` diretamente para poder destruí-lo na troca de participante; não usa o helper genérico `drawChart`.)

- [ ] **Step 7: Verificação manual do frontend**

Run: `make runserver` (em outro terminal: worker `poetry run python -m src.manage run_projection_worker`). Abrir `/rankings/pool/<slug>/dashboard/` logado como participante.
Expected: uma linha laranja só; `<select>` lista todos os participantes; trocar a seleção redesenha instantaneamente sem recarregar; participante sem histórico → estado vazio.

- [ ] **Step 8: Commit**

```bash
git add src/rankings/services/dashboard.py src/rankings/templates/rankings/dashboard_overview.html src/rankings/static/rankings/dashboard.js src/rankings/tests.py
git commit -m "$(cat <<'EOF'
feat(rankings): evolução single-user com seletor (Seção A)

Payload guarda evolution_all (todos os elegíveis); overlay marca
current_participant_id; o card ganha um <select> que troca a série em
memória sem novo fetch. Acaba o espaguete de até 11 linhas.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

______________________________________________________________________

## Task 4: Seção C1 — KPIs e aproveitamento ao vivo

Posição/gap/líder/aproveitamento passam a ser calculados por request a partir de `build_pool_leaderboard` — a mesma fonte da página de ranking — então a posição do KPI fica idêntica à do ranking por construção. Esses campos saem do payload cacheado.

**Files:**

- Modify: `src/rankings/services/dashboard.py` (overlay ao vivo; enxugar payload; remover `_kpis_from_payload`/`_utilization_overlay`)
- Test: `src/rankings/tests.py` (`DashboardServiceTest`, `DashboardCacheTest`)

**Interfaces:**

- Consumes: `build_pool_leaderboard(pool)`; `_utilization_inputs(pool, finished_matches) -> (max_points_by_id, denominator)`; `_finished_matches(season)`; `_utilization_pct(points, denominator)`.

- Produces:

  - `_kpis_live(leaderboard, participant, denominator, max_points_by_id) -> dict` com chaves `position, points, gap_to_leader, is_leader, utilization`.
  - `_utilization_live(leaderboard, participant, denominator, max_points_by_id) -> {has_data, rows}` (rows top-N com `is_current_user`).
  - Payload pool-wide enxuto: **só** `evolution_all` + `hall_of_fame` (+ `version` na Task 5). Saem `leader_points, denominator, positions, username_by_id, max_points_by_id, utilization_rows`.

- [ ] **Step 1: Atualizar o teste de paridade — deve falhar**

Em `src/rankings/tests.py`, substituir `test_overlay_matches_freshly_built_payload` (linha ~1672) por:

```python
    def test_overlay_matches_freshly_built_payload(self):
        # Hall/evolução vêm do cache; KPIs/aproveitamento são ao vivo.
        cached = build_dashboard_data(pool=self.pool, participant=self.participant)
        fresh_payload = build_dashboard_pool_payload(pool=self.pool)
        self.assertEqual(cached["hall_of_fame"], fresh_payload["hall_of_fame"])
        self.assertEqual(set(fresh_payload), {"evolution_all", "hall_of_fame", "version"})
        # Aproveitamento ao vivo coerente com a posição do leaderboard.
        self.assertTrue(cached["utilization"]["has_data"] in (True, False))
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests.DashboardCacheTest.test_overlay_matches_freshly_built_payload --verbosity=2`
Expected: FAIL (payload ainda tem as chaves antigas; sem `version`).

- [ ] **Step 3: Overlay ao vivo + helpers**

Em `src/rankings/services/dashboard.py`:

(a) Substituir `_overlay_participant` (linhas ~99-106) por:

```python
def _overlay_participant(pool, participant, payload):
    leaderboard = build_pool_leaderboard(pool)
    finished_matches = _finished_matches(pool.season)
    max_points_by_id, denominator = _utilization_inputs(pool, finished_matches)
    return {
        "progress": _progress(pool),
        "kpis": _kpis_live(leaderboard, participant, denominator, max_points_by_id),
        "evolution": _evolution_overlay(payload, participant),
        "utilization": _utilization_live(leaderboard, participant, denominator, max_points_by_id),
        "hall_of_fame": payload["hall_of_fame"],
    }
```

(b) Substituir `_kpis_from_payload` (linhas ~186-198) por `_kpis_live`:

```python
def _kpis_live(leaderboard, participant, denominator, max_points_by_id):
    positions = {row.participant.id: row.position for row in leaderboard}
    leader_points = leaderboard[0].participant.total_points if leaderboard else 0
    position = positions.get(participant.id)
    gap = max(leader_points - participant.total_points, 0)
    user_pct = _utilization_pct(max_points_by_id.get(participant.id, 0), denominator)
    return {
        "position": position,
        "points": participant.total_points,
        "gap_to_leader": gap,
        "is_leader": position == 1,
        "utilization": user_pct,
    }
```

(c) Substituir `_utilization_rows` + `_utilization_overlay` (linhas ~247-263) por uma única `_utilization_live`:

```python
def _utilization_live(leaderboard, participant, denominator, max_points_by_id):
    rows = [
        {
            "participant_id": row.participant.id,
            "label": row.participant.user.username,
            "percent": _utilization_pct(max_points_by_id.get(row.participant.id, 0), denominator),
            "is_current_user": row.participant.id == participant.id,
        }
        for row in leaderboard
    ]
    rows.sort(key=lambda item: item["percent"], reverse=True)
    return {"has_data": bool(denominator), "rows": rows[:UTILIZATION_TOP_N]}
```

(d) Enxugar `build_dashboard_pool_payload` — o `return` (editado na Task 3) passa a:

```python
    return {
        "evolution_all": _series_for_ids(pool, eligible_ids, username_by_id),
        "hall_of_fame": _hall_of_fame(pool, eligible_ids, username_by_id, leaderboard, finished_ids),
    }
```

(O `version` entra na Task 5. `max_points_by_id`/`denominator` continuam calculados no corpo porque `_utilization_inputs` é usado lá só se necessário — se ficarem sem uso após este passo, removê-los do corpo da função para o ruff não acusar variável não usada. `username_by_id`/`eligible_ids`/`finished_ids` continuam em uso pelos builders do hall e por `_series_for_ids`.)

(e) Simplificar `_normalize_payload` (linhas ~89-96) — não há mais mapas keyed-por-id no payload:

```python
def _normalize_payload(payload):
    """Sem mais mapas keyed-por-id no payload; identidade preservada."""
    return payload
```

- [ ] **Step 4: Rodar e ver passar**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests.DashboardServiceTest src.rankings.tests.DashboardCacheTest --verbosity=2`
Expected: PASS. Os testes `test_kpis_for_logged_user`, `test_kpis_leader_flag`, `test_utilization_ranked_desc_with_user_flag` continuam válidos (mesmos números, agora ao vivo). Se `test_match_score_signal_recomputes_dashboard_via_workers` (linha ~1670) afirmar `snapshot.payload["evolution_series"]`, trocar para `snapshot.payload["evolution_all"]`.

- [ ] **Step 5: Regressão + lint**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests --verbosity=2` e `make lint`
Expected: PASS; sem variáveis não usadas em `dashboard.py`.

- [ ] **Step 6: Commit**

```bash
git add src/rankings/services/dashboard.py src/rankings/tests.py
git commit -m "$(cat <<'EOF'
fix(rankings): KPIs e aproveitamento ao vivo (Seção C1)

Posição/gap/líder/aproveitamento vêm de build_pool_leaderboard por request
(mesma fonte do ranking) — KPI passa a bater com o ranking. Payload cacheado
fica só com evolution_all + hall_of_fame.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

______________________________________________________________________

## Task 5: Seção C3 — guard de frescor na leitura

Rede de segurança: o payload guarda um token de versão barato; na leitura, se a versão ao vivo diverge, enfileira o rebuild (não bloqueante) e serve o cache atual desta vez. Auto-cura também em joins, pagamento e edições manuais — não só placares. Trata payloads de formato antigo (pós-deploy) reconstruindo na hora.

**Files:**

- Modify: `src/rankings/services/dashboard.py` (`_pool_version_tuple`, `build_dashboard_pool_payload`, `_get_or_build_pool_payload`)
- Test: `src/rankings/tests.py` (`DashboardCacheTest`)

**Interfaces:**

- Consumes: `enqueue_dashboard_snapshot(pool)`.

- Produces:

  - `_pool_version_tuple(pool) -> list[int]` = `[nº jogos finalizados, nº participantes ativos, maior round_index do histórico]` (mesma função no build e na leitura, garantindo igualdade).
  - Payload pool-wide ganha a chave `version`.
  - `_get_or_build_pool_payload(pool)`: cache hit com versão igual → serve; versão diferente → enfileira rebuild + serve cache; formato antigo (sem `evolution_all`) → reconstrói síncrono.

- [ ] **Step 1: Escrever o teste que falha**

Em `src/rankings/tests.py`, dentro de `DashboardCacheTest`:

```python
    def test_stale_version_enqueues_rebuild_and_serves_cache(self):
        # Prime o cache.
        build_dashboard_data(pool=self.pool, participant=self.participant)
        self.assertFalse(
            PoolDashboardSnapshotJob.objects.filter(pool=self.pool).exists()
        )

        # Novo jogo finalizado muda a versão SEM passar pelo recompute.
        new_match = _make_match(self.season, self.stage, number=2, kickoff=timezone.now())
        Match.objects.filter(pk=new_match.pk).update(
            home_score=1, away_score=0, status=Match.STATUS_FINISHED
        )

        build_dashboard_data(pool=self.pool, participant=self.participant)
        # Guard detectou a divergência e enfileirou o rebuild (não bloqueante).
        self.assertTrue(
            PoolDashboardSnapshotJob.objects.filter(pool=self.pool).exists()
        )

    def test_version_matches_does_not_enqueue(self):
        build_dashboard_data(pool=self.pool, participant=self.participant)
        PoolDashboardSnapshotJob.objects.filter(pool=self.pool).delete()
        build_dashboard_data(pool=self.pool, participant=self.participant)
        self.assertFalse(
            PoolDashboardSnapshotJob.objects.filter(pool=self.pool).exists()
        )
```

(`Match.objects.filter(...).update(...)` evita disparar o `post_save`, isolando o guard.)

- [ ] **Step 2: Rodar e ver falhar**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests.DashboardCacheTest.test_stale_version_enqueues_rebuild_and_serves_cache --verbosity=2`
Expected: FAIL (nenhum job enfileirado; não há guard de versão).

- [ ] **Step 3: Token de versão**

Em `src/rankings/services/dashboard.py`:

(a) Adicionar os imports no topo:

```python
from django.db.models import Count, Max, Q, Sum
```

(substitui o `from django.db.models import Count, Sum` existente — acrescenta `Max` e `Q`).

(b) Adicionar o helper de versão (perto de `_finished_matches`):

```python
def _pool_version_tuple(pool):
    """Token barato p/ detectar cache velho: (nº finalizados, nº elegíveis, maior round_index).
    JSON guarda como lista; retornamos lista p/ comparar sem mismatch tupla/lista.
    """
    from src.pool.models import PoolParticipant

    finished = (
        Match.objects.filter(season=pool.season)
        .filter(Q(status=Match.STATUS_FINISHED) | (Q(home_score__isnull=False) & Q(away_score__isnull=False)))
        .count()
    )
    eligible = PoolParticipant.objects.filter(pool=pool, is_active=True).count()
    max_round = PoolRankingHistory.objects.filter(pool=pool).aggregate(value=Max("round_index"))["value"] or 0
    return [finished, eligible, max_round]
```

(c) Em `build_dashboard_pool_payload`, acrescentar `version` ao `return`:

```python
    return {
        "evolution_all": _series_for_ids(pool, eligible_ids, username_by_id),
        "hall_of_fame": _hall_of_fame(pool, eligible_ids, username_by_id, leaderboard, finished_ids),
        "version": _pool_version_tuple(pool),
    }
```

- [ ] **Step 4: Guard na leitura**

Substituir `_get_or_build_pool_payload` (linhas ~77-86) por:

```python
def _get_or_build_pool_payload(pool):
    from src.rankings.models import PoolDashboardSnapshot
    from src.rankings.services.dashboard_queue import enqueue_dashboard_snapshot

    snapshot = PoolDashboardSnapshot.objects.filter(pool=pool).first()
    if snapshot is not None and "evolution_all" in snapshot.payload:
        if snapshot.payload.get("version") == _pool_version_tuple(pool):
            return _normalize_payload(snapshot.payload)
        # Cache velho: dispara rebuild assíncrono e serve o cache atual desta vez.
        # KPIs/posição/aproveitamento já são ao vivo; só hall/evolução ficam
        # momentaneamente velhos, corrigidos no próximo load (~1s).
        enqueue_dashboard_snapshot(pool)
        return _normalize_payload(snapshot.payload)

    # Sem cache ou payload de formato antigo (pós-deploy): reconstrói agora.
    payload = build_dashboard_pool_payload(pool=pool)
    PoolDashboardSnapshot.objects.update_or_create(pool=pool, defaults={"payload": payload})
    return payload
```

- [ ] **Step 5: Rodar e ver passar**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests.DashboardCacheTest --verbosity=2`
Expected: PASS (incl. os dois novos testes). `test_second_access_reuses_cache_and_is_stale_until_recompute` continua válido (mexer em `exact_score_hits` não muda o token de versão → segue servindo o cache).

- [ ] **Step 6: Regressão + lint**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests --verbosity=2` e `make lint`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/rankings/services/dashboard.py src/rankings/tests.py
git commit -m "$(cat <<'EOF'
feat(rankings): guard de frescor na leitura da dashboard (Seção C3)

Payload guarda um token de versão (finalizados, elegíveis, maior round);
na leitura, versão divergente enfileira rebuild e serve o cache atual.
Auto-cura em joins/pagamento/edições, não só placares; payload antigo
pós-deploy é reconstruído na hora.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

______________________________________________________________________

## Task 6: Seção D — Hall da Fama: Pegando Fogo + rótulos dos cards

Corrige o único bug de lógica do hall (`_longest_streak` pula buracos de jogos sem palpite) e melhora os rótulos: Dia Iluminado mostra a data, Pé Frio deixa explícito "jogos finalizados sem pontuar", Ioiô ganha texto claro. Demais troféus já se corrigem por B+C.

**Files:**

- Modify: `src/rankings/services/dashboard.py` (`_hall_of_fame`, `_longest_streak`)
- Modify: `src/rankings/static/rankings/dashboard.js` (config `HALL`, `buildTrophy`)
- Test: `src/rankings/tests.py` (`DashboardServiceTest`)

**Interfaces:**

- Consumes: `_finished_matches`/lista cronológica de `Match`; `PoolBetScore`.

- Produces: `_longest_streak(eligible_ids, username_by_id, finished_matches) -> entry|None` — maior sequência de jogos finalizados **consecutivos** com pontos > 0, quebrando em jogo zerado **e** em jogo sem palpite.

- [ ] **Step 1: Escrever o teste que falha (bug do buraco)**

Em `src/rankings/tests.py`, dentro de `DashboardServiceTest`, adicionar:

```python
    def test_longest_streak_breaks_on_missing_bet(self):
        import datetime

        # 3 jogos finalizados cronológicos. p1 pontua no 1 e no 3, mas NÃO
        # apostou no 2 (jogo do meio). A sequência real é 1 (não pode pular o buraco).
        day = timezone.make_aware(datetime.datetime(2026, 6, 10, 10, 0))
        m_a = self.m1  # já finalizado
        m_b = self.m2  # já finalizado
        m_c = Match.objects.create(
            fifa_id="DASH-M4", season=self.season, stage=self.stage, match_number=4,
            match_date_utc=day + timedelta(hours=12), match_date_local=day + timedelta(hours=12),
            match_date_brasilia=day + timedelta(hours=12),
            home_score=0, away_score=0, status=Match.STATUS_FINISHED,
        )
        # Isola um participante novo p/ não colidir com os scores do setUp.
        u = User.objects.create_user(username="streak-u", email="su@example.com", password="123456Aa!")
        p = PoolParticipant.objects.create(pool=self.pool, user=u, is_active=True, total_points=50)
        self._score(p, m_a, 25)   # pontua
        # m_b: SEM palpite (não cria bet/score)
        self._score(p, m_c, 25)   # pontua

        hof = build_dashboard_data(pool=self.pool, participant=p)["hall_of_fame"]
        self.assertEqual(hof["longest_streak"]["username"], "streak-u")
        self.assertEqual(hof["longest_streak"]["value"], 1)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests.DashboardServiceTest.test_longest_streak_breaks_on_missing_bet --verbosity=2`
Expected: FAIL — a lógica antiga ignora o jogo sem palpite e devolve `value == 2`.

- [ ] **Step 3: Threading de `finished_matches` no hall**

Em `src/rankings/services/dashboard.py`:

(a) `build_dashboard_pool_payload` já calcula `finished_matches`. Passar a lista (não só os ids) ao hall — alterar a chamada dentro do `return`:

```python
        "hall_of_fame": _hall_of_fame(pool, eligible_ids, username_by_id, leaderboard, finished_matches),
```

(b) Atualizar a assinatura/corpo de `_hall_of_fame` (linha ~266):

```python
def _hall_of_fame(pool, eligible_ids, username_by_id, leaderboard, finished_matches):
    finished_ids = [match.id for match in finished_matches]
    return {
        "exact_scores": _king_of_scores(eligible_ids, username_by_id),
        "biggest_climb": _biggest_climb(pool, eligible_ids, username_by_id),
        "longest_streak": _longest_streak(eligible_ids, username_by_id, finished_matches),
        "best_day": _best_day(pool, eligible_ids, username_by_id),
        "pe_frio": _pe_frio(eligible_ids, username_by_id, finished_ids),
        "lanterna": _lanterna(leaderboard, username_by_id),
        "maior_queda": _maior_queda(pool, eligible_ids, username_by_id),
        "ioio": _ioio(pool, eligible_ids, username_by_id),
    }
```

- [ ] **Step 4: Reescrever `_longest_streak`**

Substituir `_longest_streak` (linhas ~329-357) por:

```python
def _longest_streak(eligible_ids, username_by_id, finished_matches):
    """Maior sequência de jogos finalizados CONSECUTIVOS com pontos > 0.

    Percorre os jogos finalizados em ordem cronológica e quebra a sequência tanto
    num jogo zerado quanto num jogo SEM palpite (sem linha de PoolBetScore) — não
    pula buracos como a versão antiga, que só via as linhas existentes.
    """
    if not finished_matches:
        return None

    ordered = sorted(finished_matches, key=lambda match: (match.match_date_utc, match.match_number, match.id))
    match_ids = [match.id for match in ordered]

    points_by_key = {
        (row["bet__participant_id"], row["bet__match_id"]): row["points"]
        for row in PoolBetScore.objects.filter(
            bet__participant_id__in=eligible_ids,
            bet__match_id__in=match_ids,
        ).values("bet__participant_id", "bet__match_id", "points")
    }

    best_id = None
    best_streak = 0
    for pid in eligible_ids:  # ordem do leaderboard = desempate determinístico
        current = 0
        for mid in match_ids:
            pts = points_by_key.get((pid, mid))
            if pts and pts > 0:
                current += 1
                if current > best_streak:
                    best_streak = current
                    best_id = pid
            else:
                current = 0

    if best_id is None or best_streak <= 0:
        return None
    return _entry(username_by_id.get(best_id, ""), best_streak)
```

- [ ] **Step 5: Rodar e ver passar (backend hall)**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests.DashboardServiceTest --verbosity=2`
Expected: PASS, incl. o novo teste e o antigo `test_hall_of_fame_highlights` (p2 = streak 2, sem buracos, continua válido).

- [ ] **Step 6: Frontend — rótulos do Hall**

Em `src/rankings/static/rankings/dashboard.js`:

(a) Adicionar um helper de data (perto de `fmtPercent`, ~linha 77):

```javascript
    function fmtDia(iso) {
        var parts = String(iso || "").split("-");
        return parts.length === 3 ? parts[2] + "/" + parts[1] : "";
    }
```

(b) Ajustar o config `HALL` (linhas ~22-31): trocar a entrada `best_day` para exibir a data (via `sub`), o hint de `pe_frio` e o de `ioio`:

```javascript
        { key: "best_day", emoji: "☀️", label: "Dia Iluminado", hint: "maior pontuação num só dia", accent: "text-yellow-400", fmt: function (v) { return v + " pts"; }, sub: function (e) { return e && e.day ? "em " + fmtDia(e.day) : ""; } },
```

```javascript
        { key: "pe_frio", emoji: "🥶", label: "Pé Frio", hint: "jogos finalizados sem pontuar", accent: "text-sky-400", fmt: function (v) { return v; } },
```

```javascript
        { key: "ioio", emoji: "🪀", label: "Ioiô", hint: "oscilou muito (soma das mudanças de posição)", accent: "text-pink-400", fmt: function (v) { return v; } },
```

(c) Em `buildTrophy` (linhas ~246-279), inserir a sub-linha opcional **antes** do hint. Acrescentar, logo após criar/abastecer `value` e antes de criar `hint`:

```javascript
        var subText = cfg.sub ? cfg.sub(entry) : "";
        var sub = null;
        if (has && subText) {
            sub = document.createElement("p");
            sub.className = "text-[11px] font-medium text-neutral-300";
            sub.textContent = subText;
        }
```

E no bloco de `appendChild`, inserir o `sub` entre `value` e `hint`:

```javascript
        cardEl.appendChild(emoji);
        cardEl.appendChild(label);
        cardEl.appendChild(winner);
        cardEl.appendChild(value);
        if (sub) {
            cardEl.appendChild(sub);
        }
        cardEl.appendChild(hint);
        return cardEl;
```

- [ ] **Step 7: Verificação manual do frontend**

Run: dashboard no browser (worker rodando).
Expected: card "Dia Iluminado" mostra `72 pts` + linha `em 18/06`; "Pé Frio" diz "jogos finalizados sem pontuar"; "Ioiô" com o texto novo.

- [ ] **Step 8: Regressão + lint + commit**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests --verbosity=2` e `make lint`

```bash
git add src/rankings/services/dashboard.py src/rankings/static/rankings/dashboard.js src/rankings/tests.py
git commit -m "$(cat <<'EOF'
fix(rankings): Pegando Fogo não pula buracos + rótulos do Hall (Seção D)

_longest_streak percorre jogos finalizados cronológicos e quebra a sequência
em jogo zerado E em jogo sem palpite. Card de Dia Iluminado mostra a data,
Pé Frio rotula "jogos finalizados sem pontuar", Ioiô com texto claro.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

______________________________________________________________________

## Task 7: Reparo único em produção

Comando para reconstruir os `PoolDashboardSnapshot` de forma síncrona (sem depender do worker), para a produção ficar correta logo após o deploy. Usado junto com o já existente `backfill_ranking_history --all`.

**Files:**

- Create: `src/rankings/management/commands/rebuild_dashboard_snapshots.py`
- Test: `src/rankings/tests.py` (classe nova `RebuildDashboardSnapshotsCommandTest`)

**Interfaces:**

- Consumes: `build_dashboard_pool_payload(pool=...)`; `PoolDashboardSnapshot.update_or_create`.

- Produces: comando `rebuild_dashboard_snapshots` com seletor mútuo `--pool` / `--season` / `--all`.

- [ ] **Step 1: Escrever o teste que falha**

```python
class RebuildDashboardSnapshotsCommandTest(TestCase):
    def setUp(self):
        self.pool, self.participants, self.matches = _build_pool_with_3_rounds()
        backfill_pool_history(self.pool)

    def test_rebuild_pool_writes_snapshot(self):
        out = StringIO()
        self.assertFalse(PoolDashboardSnapshot.objects.filter(pool=self.pool).exists())
        call_command("rebuild_dashboard_snapshots", pool=self.pool.slug, stdout=out)
        snap = PoolDashboardSnapshot.objects.get(pool=self.pool)
        self.assertIn("evolution_all", snap.payload)
        self.assertIn("version", snap.payload)
        self.assertIn(self.pool.slug, out.getvalue())

    def test_requires_a_selector(self):
        with self.assertRaises(CommandError):
            call_command("rebuild_dashboard_snapshots")
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests.RebuildDashboardSnapshotsCommandTest --verbosity=2`
Expected: FAIL — `CommandError: Unknown command: 'rebuild_dashboard_snapshots'`.

- [ ] **Step 3: Criar o comando**

Criar `src/rankings/management/commands/rebuild_dashboard_snapshots.py`:

```python
import logging

from django.core.management.base import BaseCommand, CommandError

from src.pool.models import Pool
from src.rankings.models import PoolDashboardSnapshot
from src.rankings.services.dashboard import build_dashboard_pool_payload

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Reconstrói (síncrono) o cache da dashboard de visão geral (PoolDashboardSnapshot)."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--pool", type=str, help="Slug do bolão.")
        group.add_argument("--season", type=int, help="ID da season: todos os bolões ativos dela.")
        group.add_argument("--all", action="store_true", help="Todos os bolões ativos.")

    def handle(self, *args, **options):
        if options.get("pool"):
            pool = Pool.objects.filter(slug=options["pool"]).first()
            if not pool:
                raise CommandError(f"Bolão '{options['pool']}' não encontrado.")
            pools = [pool]
        elif options.get("season"):
            pools = list(Pool.objects.filter(season_id=options["season"], is_active=True))
        else:  # --all
            pools = list(Pool.objects.filter(is_active=True))

        for pool in pools:
            payload = build_dashboard_pool_payload(pool=pool)
            PoolDashboardSnapshot.objects.update_or_create(pool=pool, defaults={"payload": payload})
            self.stdout.write(f"{pool.slug}: dashboard reconstruída")
        self.stdout.write(f"Concluído: {len(pools)} bolões")
        logger.info("Rebuild dashboard snapshots: %s bolões", len(pools))
```

- [ ] **Step 4: Rodar e ver passar**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests.RebuildDashboardSnapshotsCommandTest --verbosity=2`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/rankings/management/commands/rebuild_dashboard_snapshots.py src/rankings/tests.py
git commit -m "$(cat <<'EOF'
feat(rankings): comando rebuild_dashboard_snapshots p/ reparo único

Reconstrói o PoolDashboardSnapshot de forma síncrona (--pool/--season/--all),
sem depender do worker, para corrigir produção logo após o deploy.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6: Runbook de reparo (executar no deploy, não é código)**

No servidor (`ssh penninicup`, container `app`), após o deploy desta branch:

```bash
docker compose exec -T app python -m src.manage backfill_ranking_history --all
docker compose exec -T app python -m src.manage rebuild_dashboard_snapshots --all
```

Validar com a ferramenta de diagnóstico (Task 8) que `cache == fresh`.

______________________________________________________________________

## Task 8: Diagnóstico tolerante ao novo formato do payload

Após C1, o payload cacheado não tem mais `positions`/`denominator`/`utilization`. Tornar `diagnose_dashboard` resiliente para não quebrar ao comparar cache vs fresh.

**Files:**

- Modify: `src/rankings/management/commands/diagnose_dashboard.py` (seção "Cache vs fresh")

**Interfaces:**

- Consumes: `PoolDashboardSnapshot.payload` (agora `{evolution_all, hall_of_fame, version}`).

- [ ] **Step 1: Localizar a seção de comparação de cache**

Abrir `src/rankings/management/commands/diagnose_dashboard.py` e achar o bloco "Cache (PoolDashboardSnapshot...) vs fresh" que lê `payload["positions"]`, `payload["denominator"]` e `payload["utilization"]`.

- [ ] **Step 2: Guardar as chaves removidas**

Trocar os acessos diretos por `.get(...)` com defaults e pular as comparações ausentes. Exemplo de guarda no início do bloco:

```python
        cache_positions = cached_payload.get("positions")
        if cache_positions is None:
            w("  (payload novo: positions/aproveitamento agora são ao vivo — comparando só hall + version)")
        else:
            # ... comparações antigas de positions/denominador/aproveitamento ...
```

E sempre comparar `hall_of_fame` e, se presente, `version`:

```python
        w(f"  version cache={cached_payload.get('version')} fresh={fresh_payload.get('version')}")
```

- [ ] **Step 3: Validar manualmente (sem DB de teste)**

Run (contra um banco real, ou só checar import/parse):
`PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage help diagnose_dashboard`
Expected: sem erro de import; o comando carrega.

- [ ] **Step 4: Commit**

```bash
git add -f src/rankings/management/commands/diagnose_dashboard.py
git commit -m "$(cat <<'EOF'
chore(rankings): diagnose_dashboard tolera novo formato do payload

Cache não tem mais positions/aproveitamento (foram p/ live); compara só
hall + version quando o payload é o novo.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

(Nota: `diagnose_dashboard.py` está atualmente untracked; o `git add -f` não é por gitignore e sim para incluí-lo agora como ferramenta de manutenção, conforme a spec.)

______________________________________________________________________

## Self-Review

**Spec coverage:**

- Seção A (evolução single-user + seletor) → Task 3. ✓
- Seção B (snapshot as-of) → Task 1. ✓
- Seção C1 (KPIs/aproveitamento ao vivo) → Task 4. ✓
- Seção C2 (gatilho no chokepoint via `refresh_pool_derived_data`) → Task 2. ✓
- Seção C3 (guard de frescor + token de versão) → Task 5. ✓
- Seção D (Pegando Fogo + rótulos pe_frio/best_day/ioio) → Task 6. ✓
- Reparo único (produção) → Task 7 (comando + runbook com `backfill_ranking_history --all`). ✓
- Ferramenta de diagnóstico mantida e adaptada → Task 8. ✓
- Testes listados na spec: as-of pós-correção/fora de ordem (Task 1), regressão causa #2 + posição = leaderboard (Tasks 2/4), chokepoint reconstrói histórico + enfileira (Task 2), guard C3 enfileira na divergência (Task 5), `evolution_all` + `current_participant_id` (Task 3), Pegando Fogo quebra em zero e sem palpite (Task 6). ✓

**Consistência de tipos / nomes:**

- `_evolution_overlay(payload, participant)` — assinatura nova consistente entre Task 3 (definição) e Task 4 (uso em `_overlay_participant`).
- `_hall_of_fame(pool, eligible_ids, username_by_id, leaderboard, finished_matches)` — Task 6 muda o último parâmetro de `finished_ids` p/ `finished_matches`; a chamada em `build_dashboard_pool_payload` é atualizada no mesmo Task 6 (Step 3a). Tasks 3/4 ainda passam `finished_ids` — **ordem importa**: ao chegar no Task 6, trocar a chamada para `finished_matches` (Step 3a) junto com a assinatura. Consistente desde que as tasks rodem em ordem.
- `_pool_version_tuple(pool)` — mesma função usada no build (Task 5 Step 3c) e na leitura (Step 4), garantindo igualdade do token.
- `refresh_pool_derived_data(pool)` — definido em Task 2, usado em `recalculate_all_pools` e no comando.
- Payload final: `{evolution_all, hall_of_fame, version}` — coerente entre Tasks 3/4/5 e o teste de paridade (Task 4 Step 1).

**Placeholders:** nenhum TODO/TBD; todo passo de código traz o código completo.

**Ordem de execução obrigatória:** 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 (Tasks 3-6 editam `dashboard.py` incrementalmente; rodar fora de ordem quebra as assinaturas).
