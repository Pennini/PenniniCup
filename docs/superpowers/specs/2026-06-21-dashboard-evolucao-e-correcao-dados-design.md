# Dashboard de visão geral: gráfico de evolução single-user + correção dos dados

- **Data:** 2026-06-21
- **Branch:** `feat/palpites-carrossel`
- **Arquivos-âncora:** `src/rankings/services/dashboard.py`, `src/rankings/static/rankings/dashboard.js`, `src/rankings/templates/rankings/dashboard_overview.html`, `src/rankings/services/position_snapshot.py`, `src/pool/services/ranking.py`, `src/football/services/sync_matches.py`

## Problema

A dashboard de visão geral do bolão tem dois problemas:

1. **Gráfico de evolução feio / poluído.** Desenha até 10 participantes (+ o usuário logado) como linhas sobrepostas — vira espaguete e é difícil de ler.
1. **Dados errados.** Em produção (bolão `Ramal`): a posição mostrada nos KPIs diverge da posição real do ranking, e os troféus do Hall da Fama estão errados.

## Investigação (resumo + evidência)

Criado um comando read-only `diagnose_dashboard --pool <slug>` que compara, por participante, os valores **armazenados** (agregados, `PoolBetScore`) com um recálculo **fresco**, e o **cache** (`PoolDashboardSnapshot`) com o payload fresco.

Saída em produção (`Ramal`, 33 participantes, 37 jogos finalizados):

- **Agregados e scores estão corretos:** toda linha `stored == fresh`, zero `DRIFT/STALE/MISSING`. Não há bug de cálculo de pontos.
- **O cache está velho:** `computed_at = 2026-06-20 22:13`; `DENOMINADOR cache=850 fresh=925` (cache construído com 34 jogos, agora são 37); `positions: DIFEREM` (ranking do cache ≠ ranking ao vivo); `hall` difere em `biggest_climb`, `pe_frio`, `ioio`.
- **O job de recálculo nunca foi re-enfileirado:** `PoolDashboardSnapshotJob status=IDLE requested_at=2026-06-20 22:13` — não disparou após os 3 jogos seguintes.

### Causa raiz #1 — snapshot de histórico grava valores errados

`position_snapshot.snapshot_round_for_match` (caminho ao vivo) carimba cada rodada com os **agregados atuais da temporada** (`participant.total_points`, `position` do leaderboard atual, etc.), não com os valores **as-of** daquela rodada. Fica certo numa execução cronológica limpa, mas corrompe em: correção de placar de jogo antigo, bônus de fim de torneio (campeão/artilheiro/classificados) vazando para rodadas antigas, e processamento fora de ordem (`round_index = max+1` é ordem de processamento, não cronologia). O caminho de `backfill_pool_history` (via `compute_asof_standings`) já faz o cálculo **correto**, mas só roda manualmente.

Impacto: gráfico de evolução + troféus que leem `PoolRankingHistory` (Maior Escalada, Tobogã, Ioiô).

### Causa raiz #2 — cache (e histórico) nunca atualizam no sync

`sync_matches.py:135` grava placares com `Match.objects.bulk_create(update_conflicts=True)`. **`bulk_create`/`bulk_update` não disparam `post_save`**, então o sinal `recalculate_pool_data_after_match_save` (que chama `enqueue_ranking_snapshot` → `enqueue_dashboard_snapshot`) **nunca roda durante o sync**. O sync atualiza os agregados (`recalculate_all_pools`, linha 161), mas **não** o `PoolRankingHistory` nem o `PoolDashboardSnapshot`. Como em produção os jogos terminam via sync da API, o cache congela no último `Match.save()` manual (admin). `enqueue_ranking_snapshot` não é chamado em nenhum outro lugar de produção além do sinal.

Impacto: KPIs (posição), aproveitamento e Hall da Fama servidos do cache velho.

## Escopo

**Dentro:**

- Redesenhar o gráfico de evolução para **um participante por vez**, padrão = usuário logado, com seletor para qualquer participante do bolão.
- Corrigir o snapshot de histórico (as-of) e fechar o gap de gatilho do sync.
- Tornar KPIs e aproveitamento **ao vivo** (sem cache).
- Rede de segurança: guard de frescor na leitura do cache.
- Corrigir o Hall da Fama (lógica do Pegando Fogo, data do Dia Iluminado, texto do Ioiô) — ver Seção D.
- Reparo único dos dados em produção.

**Fora:**

- Redesenho do gráfico de Aproveitamento (continua barra top-10; passa só a ser calculado ao vivo).
- Motor de pontuação (`calculate_bet_points`) — comprovadamente correto.
- Demais cards/telas.

## Design

### Seção A — Gráfico de evolução: single-user + seletor

**Backend (`dashboard.py`):**

- Remover o gating top-N (`EVOLUTION_TOP_N`, `selected_ids`, `_evolution_series`, e o merge "minha própria série" em `_evolution_overlay`).
- No payload pool-wide (cacheado), gravar `evolution_all`: uma entrada por **participante elegível com histórico** — `{participant_id, label, points: [{round, position, points}]}` — ordenada pela posição atual do leaderboard. Construída numa query via `_series_for_ids(pool, todos_os_ids, username_by_id)`.
- No overlay por participante, anexar `current_participant_id` (seleção padrão). O overlay não mexe mais nas séries (já completas no cache).

**Frontend (`dashboard_overview.html` + `dashboard.js`):**

- `<select>` no cabeçalho do card "Evolução", populado com a lista de participantes (label + id), padrão = `current_participant_id`.
- `renderEvolution` desenha **um** dataset (participante selecionado), cor laranja do usuário, eixo posição × rodada. `change` no select redesenha a partir dos dados em memória — sem novo fetch.
- Estado vazio quando o participante selecionado não tem histórico.

Resultado: uma linha limpa, troca instantânea para qualquer um. Acaba o espaguete.

### Seção B — Causa raiz #1: snapshot as-of

Unificar o caminho ao vivo no motor as-of já correto: para cada bolão afetado, `snapshot_round_for_match` chama `backfill_pool_history(pool)` (rebuild completo, idempotente, `round_index` cronológico) em vez de carimbar agregados atuais. Um único caminho correto; os troféus de ranking se auto-corrigem.

### Seção C — Causa raiz #2: frescor

**C1 — KPIs + aproveitamento ao vivo.** Em `_overlay_participant`, calcular posição, distância p/ líder, pontos do líder e aproveitamento (KPI **e** as linhas do gráfico de barras) a partir de `build_pool_leaderboard(pool)` + entradas de utilização ao vivo (`_utilization_inputs`) — a **mesma** fonte da página de ranking, então a posição do KPI passa a ser idêntica à do ranking por construção. Remover esses campos do payload cacheado. Custo: uma construção de leaderboard + ~2 queries por request, mesma ordem da página de ranking.

**C2 — fechar o gap de gatilho (também conserta o frescor do histórico da causa #1).** Centralizar o refresh de dados derivados no ponto único de recálculo por bolão (`recalculate_pool_scores` / o laço de `recalculate_all_pools`): depois de recomputar os agregados, reconstruir o histórico as-of + enfileirar o rebuild do dashboard daquele bolão. Assim o sync — e **todo** caminho de placar — atualiza histórico + cache. Introduzir um ponto de entrada único por bolão (ex.: `refresh_pool_derived_data(pool)`) usado tanto pelo sinal quanto pelo chokepoint; preferir as filas assíncronas existentes para não deixar o sync lento; garantir ordem histórico-antes-do-dashboard. O worker (poll 1s) aplica em ~1s.

**Cacheado fica só** o pesado: Hall da Fama + `evolution_all`. Ambos atualizam via C2.

**C3 — guard de frescor na leitura (rede de segurança).** Gravar no payload um token de versão barato (nº de jogos finalizados + nº de elegíveis + maior `round_index` do histórico). Em `_get_or_build_pool_payload`, calcular a versão ao vivo (barata); se diferir da gravada, `enqueue_dashboard_snapshot(pool)` (não bloqueante) e servir o cache atual desta vez — como KPIs/posição/aproveitamento já são ao vivo, o único dado momentaneamente velho é Hall/evolução, que se corrige no próximo load (~1s). Auto-cura também em joins, aprovação de pagamento e edições manuais — não só placares.

### Seção D — Hall da Fama: correções e definições

O diagnóstico (`diagnose_dashboard --participant`) confirma que os agregados estão corretos; os troféus aparecem "errados" por (a) cache velho (Seção C) e (b) histórico corrompido (Seção B). Definições escolhidas pelo dono do produto:

| Troféu                  | Decisão                                                                                      | O que muda                                     |
| ----------------------- | -------------------------------------------------------------------------------------------- | ---------------------------------------------- |
| Rei dos Placares        | mantém (lê `exact_score_hits`, sem drift)                                                    | só frescor (C)                                 |
| Maior Escalada / Tobogã | "arrancada do pior até depois" — **já é** o cálculo atual de `_biggest_climb`/`_maior_queda` | só corrigir o histórico (B)                    |
| Pé Frio                 | mantém "só apostou e zerou" (ausência não conta)                                             | **não é bug**; clarear rótulo (só finalizados) |
| Lanterna                | último do ranking                                                                            | agora ao vivo (C1)                             |
| Ioiô                    | mantém churn Σ\|Δpos\|                                                                       | corrigir histórico (B) + clarear texto do card |
| **Pegando Fogo**        | **corrigir lógica** (bug real)                                                               | ver abaixo                                     |
| **Dia Iluminado**       | mantém cálculo                                                                               | **mostrar a data no card**                     |

**Pegando Fogo (`_longest_streak`) — bug real.** Hoje itera só as linhas de `PoolBetScore` existentes ordenadas por data; um jogo **sem palpite** (sem linha) fica invisível e a sequência "pula o buraco", inflando o número. Correção: percorrer a sequência cronológica dos jogos **finalizados** e quebrar a sequência tanto num jogo zerado quanto num jogo **sem palpite**. "Pontuando" = pontos > 0 (qualquer pontuação).

**Pé Frio (`_pe_frio`) — não é bug.** Verificado no `Ramal`: a query é limpa (`points__lte=0`, só jogos finalizados), os dados estão limpos (0 palpites ausentes, nenhum `points` NULL, zero `stale`) e `eligible_ids == ranking completo` (33). Lazzo (19) é mesmo quem tem mais **jogos finalizados apostados e zerados**. A divergência que se vê no site vem de contar **jogos futuros/não finalizados** (apostados, ainda 0 pts) como "zerados" — o card conta só finalizados. Não é frescor (cache=18 vs fresh=19, ambos Lazzo, diferença trivial). Correção: rótulo do card explícito ("jogos finalizados sem pontuar"); sem mudança de lógica. (Confirmar com `--participant <nome>`: comparar `apostou e zerou`, que é o número que o card usa.)

**Dia Iluminado (`_best_day`)** — cálculo mantido (maior soma de pontos num dia Brasília; `TruncDate` já usa `TIME_ZONE`, sem off-by-one; `match_date_brasilia` é `match_date_utc.astimezone(BRASILIA_TZ)`, mesmo instante → mesmo dia). Frontend: o card passa a exibir a data (o backend já envia `day`).

**Ioiô** — número mantido (soma de todas as subidas/descidas no campeonato); melhorar o `hint`/texto do card para explicar o que ele representa.

**Frontend (`dashboard.js`):** ajustar o config `HALL` — `best_day` exibe `entry.day`; `pe_frio` com rótulo "jogos finalizados sem pontuar"; `ioio` com hint mais claro.

### Reparo único (produção)

Após o deploy: `backfill_ranking_history --all` + rebuild dos `PoolDashboardSnapshot` (enfileirar/recalcular), para a produção ficar correta imediatamente, sem esperar o próximo sync.

### Ferramenta de diagnóstico

Manter `src/rankings/management/commands/diagnose_dashboard.py` (read-only) como ferramenta de manutenção: compara stored vs fresh vs cache e estado da fila.

## Fluxo de dados (depois)

```
Sync (bulk_create placares)
  → recalculate_all_pools → recalculate_pool_scores(pool)   [agregados]
      → refresh_pool_derived_data(pool):
            backfill_pool_history(pool)        [histórico as-of, round_index cronológico]
            enqueue_dashboard_snapshot(pool)   [worker rebuild do cache pesado]

Request da dashboard (pool_dashboard_data)
  → payload pool-wide (cache: hall + evolution_all) + guard de versão (C3)
  → overlay AO VIVO: posição/gap/líder/aproveitamento (build_pool_leaderboard)
  → evolução: evolution_all do cache + current_participant_id
```

## Testes

- `snapshot_round_for_match` produz histórico igual ao `backfill_pool_history` (as-of), inclusive após correção de placar e processamento fora de ordem.
- Regressão da causa #2: após um `bulk_create`/recalc sem sinal, o overlay devolve a **mesma** posição que `build_pool_leaderboard`, e o refresh de derivados é disparado.
- Chokepoint de recálculo reconstrói histórico + enfileira dashboard.
- Guard C3 enfileira rebuild quando o token de versão diverge.
- `evolution_all` contém todos os elegíveis com histórico; overlay marca `current_participant_id`.
- Pegando Fogo: a sequência quebra num jogo zerado **e** num jogo sem palpite (não pula buracos); conta só jogos finalizados.
- Frontend: o seletor troca a série exibida sem novo fetch; Dia Iluminado mostra a data (verificação manual/representativa).

## Riscos / trade-offs

- KPIs ao vivo adicionam uma construção de leaderboard por request — barato (mesmo custo da página de ranking, que o usuário já usa).
- Rebuild as-of a cada sync é mais pesado que incremental; mitigado rodando via fila assíncrona e fora do request.
- `evolution_all` aumenta o payload do cache (todos os participantes), mas sai `positions`/`utilization`; tamanho líquido OK para bolões reais (dezenas de participantes).

## Fora de escopo / futuro

- Aposentar o `PoolRankingSnapshotJob` per-match em favor de um job per-bolão (limpeza opcional, já que o rebuild é per-bolão).
- Cache da projeção de bracket (`/pools/<slug>/`) — não relacionado.
