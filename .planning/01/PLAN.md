---
phase: 01-qualidade-base
plan: '01'
type: execute
wave: 1
depends_on: []
files_modified:
  - pyproject.toml
  - .coveragerc
  - Makefile
autonomous: true
requirements:
  - NFR-04
must_haves:
  truths:
    - coverage.py está instalado no grupo dev do Poetry
    - '`make coverage` executa os testes e imprime o relatório de cobertura sem erro de comando'
    - 'Branches excluídas do relatório: migrations e settings'
    - Um threshold mínimo está configurado em .coveragerc de forma que falhas na meta encerrem o comando com código de saída não-zero
  artifacts:
    - path: .coveragerc
      provides: Configuração de source, branch, omit e fail_under para coverage.py
      contains: '[run]'
    - path: Makefile
      provides: Target `make coverage` chamando `poetry run coverage run` e `poetry run coverage report`
      contains: coverage
  key_links:
    - from: Makefile target `coverage`
      to: poetry run coverage run
      via: variável de ambiente PENNINICUP_SETTINGS_PROFILE=test exportada no mesmo target
      pattern: PENNINICUP_SETTINGS_PROFILE
    - from: .coveragerc
      to: poetry run coverage report
      via: coverage lê .coveragerc automaticamente quando presente no diretório raiz
      pattern: fail_under
---

<objective>
Instalar coverage.py como dependência de desenvolvimento e expor um target `make coverage`
funcional no Makefile. Isso cria a base de medição de cobertura que os demais planos desta
fase vão melhorar ao adicionar novos testes.

Purpose: Sem coverage.py instalado nenhum threshold pode ser aplicado. Este plano é o
pré-requisito para os demais planos de Phase 1 — mas não bloqueia a execução em paralelo
dos planos de testes (02 e 03), pois eles não dependem desta ferramenta para rodar.

Output: pyproject.toml atualizado com `coverage` em `[tool.poetry.group.dev.dependencies]`,
arquivo `.coveragerc` na raiz, e novo target `coverage` no Makefile.
</objective>

\<execution_context>
@/root/.claude/get-shit-done/workflows/execute-plan.md
\</execution_context>

<context>
@.planning/ROADMAP.md
@.planning/REQUIREMENTS.md
@.planning/01/RESEARCH.md
</context>

<interfaces>
<!-- Makefile atual (linhas relevantes extraídas): -->
<!--
.PHONY: test
test: export PENNINICUP_SETTINGS_PROFILE = test
test:
	poetry run python -m src.manage test --settings=src.config.settings --verbosity=2

.PHONY: test-single
(ausente no Makefile atual — existe apenas no CLAUDE.md como `make test-single`)
-->

<!-- pyproject.toml: não contém `coverage` em nenhum grupo de dependências (confirmado por grep). -->

<!-- Formato de grupo dev no pyproject.toml deve seguir o padrão Poetry 1.x/2.x: -->

<!--
[tool.poetry.group.dev.dependencies]
pre-commit = "..."
ruff = "..."
# coverage será adicionado aqui
-->

</interfaces>

<tasks>

<task type="auto">
  <name>Task 1: Instalar coverage.py e criar .coveragerc</name>
  <files>pyproject.toml, poetry.lock, .coveragerc</files>
  <action>
Execute o comando abaixo para adicionar coverage ao grupo dev do Poetry:

```
poetry add --group dev coverage
```

Após a instalação, crie o arquivo `.coveragerc` na raiz do projeto com o conteúdo abaixo.
NÃO use threshold `fail_under` ainda — execute sem ele primeiro para estabelecer a baseline.
O threshold será adicionado no Task 2 depois que o Makefile estiver funcionando e o valor
de baseline for conhecido.

Conteúdo do `.coveragerc`:

```ini
[run]
source = src
branch = True
omit =
    src/*/migrations/*
    src/config/settings/*

[report]
show_missing = True

[html]
directory = htmlcov
```

Nota: `fail_under` é intencionalmente omitido neste task. Será adicionado no Task 2 após
medir a baseline. Isso evita o Pitfall 5 (threshold bloqueando CI antes de estabelecer
a baseline — ver RESEARCH.md).
</action>
<verify>
<automated>poetry run python -c "import coverage; print(coverage.__version__)"</automated>
</verify>
<done>Comando acima imprime a versão do coverage (ex: "7.x.x") sem ModuleNotFoundError. `.coveragerc` existe na raiz com seção `[run]`.</done>
</task>

<task type="auto">
  <name>Task 2: Adicionar target `make coverage` no Makefile e definir threshold</name>
  <files>Makefile, .coveragerc</files>
  <action>
Adicione o target `coverage` ao Makefile DEPOIS do target `test` existente.
Use exatamente o mesmo padrão de exportação de variável de ambiente do target `test`:

```makefile
.PHONY: coverage
coverage: export PENNINICUP_SETTINGS_PROFILE = test
coverage:
	poetry run coverage run \
		-m src.manage test --settings=src.config.settings --verbosity=2
	poetry run coverage report
	poetry run coverage html
```

Observe que `.coveragerc` já define `source`, `branch` e `omit` — não repita inline no comando.

Em seguida, execute `make coverage` UMA VEZ para medir a baseline atual:

```
make coverage
```

Leia a linha "TOTAL" no output (ex: "TOTAL 1234 456 37%"). Com esse número em mãos,
adicione `fail_under` ao `.coveragerc` com o valor da baseline ARREDONDADO PARA BAIXO
ao múltiplo de 5 mais próximo (ex: 37% → 35). Isso trava o floor sem exigir novos testes
para este plano:

```ini
[report]
show_missing = True
fail_under = 35
```

Substitua 35 pelo valor real observado. O floor será elevado pelos Planos 02 e 03
à medida que novos testes são adicionados.

IMPORTANTE: A indentação das receitas do Makefile deve ser TAB (não espaços) — o make
falha com "missing separator" se for espaço.
</action>
<verify>
<automated>make coverage</automated>
</verify>
<done>`make coverage` completa com código de saída 0. O relatório exibe a linha "TOTAL" com percentual. O arquivo `htmlcov/index.html` foi gerado. `.coveragerc` contém `fail_under` com o valor da baseline.</done>
</task>

</tasks>

\<threat_model>

## Trust Boundaries

| Boundary         | Descrição                                                                    |
| ---------------- | ---------------------------------------------------------------------------- |
| Makefile → shell | Comandos do Makefile executam com permissões do shell do desenvolvedor local |

## STRIDE Threat Register

| Threat ID        | Category               | Component                      | Disposition | Mitigation Plan                                                                                                 |
| ---------------- | ---------------------- | ------------------------------ | ----------- | --------------------------------------------------------------------------------------------------------------- |
| T-01-01-01       | Tampering              | `.coveragerc` `fail_under`     | accept      | Valor de threshold está em controle de versão; qualquer redução é visível via diff                              |
| T-01-01-02       | Information Disclosure | `htmlcov/` gerado na raiz      | accept      | Diretório de cobertura HTML é artefato local de desenvolvimento; adicionar ao `.gitignore` se ainda não estiver |
| T-01-01-03       | Elevation of Privilege | `poetry add` sem `--group dev` | mitigate    | Sempre usar `poetry add --group dev coverage` para não poluir dependências de produção                          |
| \</threat_model> |                        |                                |             |                                                                                                                 |

<verification>
1. `poetry run python -c "import coverage"` retorna sem erro
2. `cat .coveragerc` mostra `[run]`, `source = src`, `branch = True`, `omit` com migrations e settings
3. `make coverage` termina com código 0 e exibe linha "TOTAL"
4. `ls htmlcov/index.html` confirma geração do relatório HTML
5. `.coveragerc` contém `fail_under` com valor numérico positivo
</verification>

\<success_criteria>

- coverage.py instalado e importável no ambiente Poetry do projeto
- `make coverage` executa os testes e produz relatório de cobertura com threshold mínimo configurado
- Threshold definido na baseline observada (não arbitrário), garantindo que regressões de cobertura sejam detectáveis
  \</success_criteria>

<output>
Após conclusão, crie `.planning/phases/01-qualidade-base/01-01-SUMMARY.md` com:
- Versão do coverage instalada
- Percentual de cobertura baseline medido (valor TOTAL do primeiro `make coverage`)
- Valor de `fail_under` configurado em `.coveragerc`
- Qualquer problema encontrado durante a instalação
</output>
---
phase: "01-qualidade-base"
plan: "02"
type: execute
wave: 1
depends_on: []
files_modified:
  - src/pool/tests.py
autonomous: true
requirements:
  - FR-02
  - FR-04
  - NFR-04
must_haves:
  truths:
    - "Todos os branches de `calculate_bet_points` são exercitados por testes diretos"
    - "Todos os branches de `normalize_stage_key` (variantes PT e EN) são cobertos"
    - "`phase_for_match` tem ao menos um teste direto para o caminho KNOCKOUT"
    - "Nenhum teste neste plano usa o banco de dados — todos são `SimpleTestCase`"
    - "Os novos testes passam em `make test` sem erros"
  artifacts:
    - path: "src/pool/tests.py"
      provides: "Quatro novas classes de teste anexadas ao arquivo existente"
      contains: "ScoringWinnerFromScoreTest"
  key_links:
    - from: "ScoringCalculateBetPointsTest"
      to: "src/pool/services/scoring.calculate_bet_points"
      via: "import direto; SimpleNamespace substitui bet/match/scoring_config"
      pattern: "from src.pool.services.scoring import"
    - from: "NormalizeStageKeyTest"
      to: "src/pool/services/rules.normalize_stage_key"
      via: "import direto; SimpleNamespace(name=...) substitui Stage ORM"
      pattern: "from src.pool.services.rules import"
---

<objective>
Adicionar testes unitários diretos para `scoring.py` (função `_winner_from_score` e
`calculate_bet_points`) e `rules.py` (funções `normalize_stage_key` e `phase_for_match`).

Todos são funções puras sem acesso ao banco de dados. Os testes usam `SimpleTestCase` com
`SimpleNamespace` como substituto leve dos objetos ORM — sem fixtures, sem setUp de banco,
sem factory library.

Purpose: Cobrir os branches não testados identificados no RESEARCH.md (empate 0-0,
bet inativa, bônus inativo no mata-mata, variantes PT da API FIFA, stage None/name None).

Output: Quatro classes de teste anexadas ao final de `src/pool/tests.py`:
`ScoringWinnerFromScoreTest`, `ScoringCalculateBetPointsTest`,
`NormalizeStageKeyTest`, `PhaseForMatchTest`.
</objective>

\<execution_context>
@/root/.claude/get-shit-done/workflows/execute-plan.md
\</execution_context>

<context>
@.planning/ROADMAP.md
@.planning/01/RESEARCH.md
@src/pool/tests.py
</context>

<interfaces>
<!-- Assinaturas exatas das funções a serem testadas (lidas de src/pool/services/scoring.py e rules.py): -->

<!-- scoring.py -->

```python
def _winner_from_score(home_score, away_score) -> str:
    # Retorna "HOME", "AWAY" ou "DRAW"
    # Sem acesso a DB; sem imports externos além de rules.py

def calculate_bet_points(bet, scoring_config) -> dict:
    # bet: objeto com atributos is_active, home_score_pred, away_score_pred,
    #      winner_pred_id, match (objeto com stage, home_score, away_score, winner_id)
    # scoring_config: objeto com group_winner_or_draw_points, group_exact_score_points,
    #      group_one_team_score_points, knockout_winner_advancing_points,
    #      knockout_exact_score_points, knockout_one_team_score_points
    # Retorna: {"points": int, "exact_score": bool, "winner_or_draw": bool,
    #           "winner_advancing": bool, "one_team_score": bool}
```

<!-- rules.py -->

```python
PHASE_GROUP = "GROUP"
PHASE_KNOCKOUT = "KNOCKOUT"

def normalize_stage_key(stage) -> str:
    # stage: objeto com atributo .name (str ou None), ou None/falsy
    # Retorna: "GROUP", "SF", "QF", "R16", "R32", "THIRD", "FINAL" ou ""

def phase_for_match(match) -> str:
    # match: objeto com atributo .stage (passado para normalize_stage_key)
    # Retorna PHASE_GROUP ou PHASE_KNOCKOUT
```

<!-- Padrão de import verificado no arquivo scoring.py: -->

```python
from src.pool.services.rules import PHASE_GROUP, phase_for_match
```

<!-- `_winner_from_score` é função privada — importar com: -->

```python
from src.pool.services.scoring import _winner_from_score, calculate_bet_points
```

</interfaces>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Testes para scoring.py — `_winner_from_score` e `calculate_bet_points`</name>
  <files>src/pool/tests.py</files>
  <behavior>
    Comportamentos esperados para `_winner_from_score`:
    - _winner_from_score(2, 1) == "HOME"
    - _winner_from_score(0, 1) == "AWAY"
    - _winner_from_score(1, 1) == "DRAW"
    - _winner_from_score(0, 0) == "DRAW"  # empate zerozero

```
Comportamentos esperados para `calculate_bet_points` (early returns):
- bet.is_active=False → points=0, exact_score=False, winner_or_draw=False
- bet.home_score_pred=None → points=0, todos False
- match.home_score=None → points=0, todos False

Comportamentos esperados para `calculate_bet_points` (fase de grupos — stage.name="Group Stage"):
- placar exato (2-1 pred vs 2-1 real) → points=10, exact_score=True, winner_or_draw=True
- vencedor correto mas não exato (2-1 pred vs 3-1 real) → points=6, winner_or_draw=True, exact_score=False
- um time correto, não exato (2-1 pred vs 2-0 real) → points=2, one_team_score=True
- empate predito e real (1-1 pred vs 0-0 real) → points=6, winner_or_draw=True (ambos DRAW)
- tudo errado (2-1 pred vs 0-1 real) → points=0

Comportamentos esperados para `calculate_bet_points` (mata-mata — stage.name="Semi-Final"):
- winner_pred_id correto + placar exato → winner_advancing=True, points=8+6=14
- winner_pred_id correto + placar errado (sem one_team) → points=8, winner_advancing=True
- winner_pred_id errado → winner_advancing=False, points=0 (sem acerto de placar)
- placar exato mas winner_pred_id errado → exact_score=True, winner_advancing=False, points=6
- bet.is_active=False em mata-mata (bônus inativo) → points=0, winner_advancing=False
```

</behavior>
  <action>
Anexe ao final de `src/pool/tests.py` as classes `ScoringWinnerFromScoreTest` e
`ScoringCalculateBetPointsTest`. Siga as convenções do arquivo existente:
- Use `from django.test import SimpleTestCase` (NÃO `TestCase` — sem DB)
- Use `from types import SimpleNamespace`
- Imports no topo do bloco adicionado, junto com os imports já existentes OU agrupados
  no início das novas classes se o arquivo já tem imports definidos

Estrutura dos helpers (copie esse padrão):

```python
class ScoringCalculateBetPointsTest(SimpleTestCase):
    def _make_scoring_config(self, **overrides):
        defaults = dict(
            group_winner_or_draw_points=6,
            group_exact_score_points=4,
            group_one_team_score_points=2,
            knockout_winner_advancing_points=8,
            knockout_exact_score_points=6,
            knockout_one_team_score_points=2,
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def _make_group_bet(self, home_pred, away_pred, home_real, away_real, is_active=True):
        stage = SimpleNamespace(name="Group Stage")
        match = SimpleNamespace(
            stage=stage,
            home_score=home_real,
            away_score=away_real,
            winner_id=None,
        )
        return SimpleNamespace(
            is_active=is_active,
            home_score_pred=home_pred,
            away_score_pred=away_pred,
            winner_pred_id=None,
            match=match,
        )

    def _make_knockout_bet(
        self, home_pred, away_pred, home_real, away_real, winner_real_id=None, winner_pred_id=None, is_active=True
    ):
        stage = SimpleNamespace(name="Semi-Final")
        match = SimpleNamespace(
            stage=stage,
            home_score=home_real,
            away_score=away_real,
            winner_id=winner_real_id,
        )
        return SimpleNamespace(
            is_active=is_active,
            home_score_pred=home_pred,
            away_score_pred=away_pred,
            winner_pred_id=winner_pred_id,
            match=match,
        )
```

Crie um método de teste por comportamento listado em `<behavior>`. Nomeie os métodos em
inglês seguindo o padrão `test_<descricao_curta>` (o arquivo existente usa inglês nos
nomes de método).

NÃO use banco de dados. NÃO instancie modelos ORM. Se `SimpleTestCase` levantar
`AssertionError: Database queries are not allowed`, revise para garantir que nenhum
ORM foi tocado.
</action>
<verify>
<automated>PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.ScoringWinnerFromScoreTest src.pool.tests.ScoringCalculateBetPointsTest --settings=src.config.settings --verbosity=2</automated>
</verify>
<done>Todos os métodos de teste das duas classes passam (0 erros, 0 falhas). O output mostra cada método individualmente com "ok".</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Testes para rules.py — `normalize_stage_key` e `phase_for_match`</name>
  <files>src/pool/tests.py</files>
  <behavior>
    Comportamentos esperados para `normalize_stage_key`:
    - stage=None → ""
    - stage=SimpleNamespace(name=None) → ""
    - stage=SimpleNamespace(name="") → ""
    - stage=SimpleNamespace(name="Group Stage") → "GROUP"  (EN)
    - stage=SimpleNamespace(name="Grupo A") → "GROUP"  (PT - GRUPO)
    - stage=SimpleNamespace(name="Primeira Fase") → "GROUP"  (PT - PRIMEIRA FASE)
    - stage=SimpleNamespace(name="Round of 16") → "R16"  (EN)
    - stage=SimpleNamespace(name="Oitavas de Final") → "R16"  (PT)
    - stage=SimpleNamespace(name="Quarter-Final") → "QF"  (EN — hífen vira espaço na normalização)
    - stage=SimpleNamespace(name="Quartas de Final") → "QF"  (PT - QUART)
    - stage=SimpleNamespace(name="Semi-Final") → "SF"  (EN)
    - stage=SimpleNamespace(name="Semifinal") → "SF"  (PT)
    - stage=SimpleNamespace(name="Decisão 3o Lugar") → "THIRD"
    - stage=SimpleNamespace(name="Terceiro Lugar") → "THIRD"
    - stage=SimpleNamespace(name="Final") → "FINAL"  (exato case-insensitive)
    - stage=SimpleNamespace(name="Grand Final") → "FINAL"  (FINAL sem SEMI/QUART/OITAV)
    - stage=SimpleNamespace(name="Mystery Stage") → ""  (nenhum branch casado)

```
Comportamentos esperados para `phase_for_match`:
- match com stage.name="Group Stage" → PHASE_GROUP ("GROUP")
- match com stage.name="Semi-Final" → PHASE_KNOCKOUT ("KNOCKOUT")
- match com stage=None → PHASE_KNOCKOUT (fallback: normalize_stage_key retorna "", não "GROUP")
```

</behavior>
  <action>
Anexe ao final de `src/pool/tests.py` (após as classes do Task 1) as classes
`NormalizeStageKeyTest` e `PhaseForMatchTest`.

Use `SimpleTestCase`. Use o helper `_stage(name)` para reduzir repetição:

```python
class NormalizeStageKeyTest(SimpleTestCase):
    def _stage(self, name):
        return SimpleNamespace(name=name)

    # ... métodos de teste
```

Crie um método de teste por linha do `<behavior>`. Nomeie seguindo o padrão do arquivo.

Para `PhaseForMatchTest`, o helper de match é:

```python
def _match(self, stage_name):
    return SimpleNamespace(stage=SimpleNamespace(name=stage_name) if stage_name is not None else None)
```

Não use banco de dados nem ORM.
</action>
<verify>
<automated>PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.NormalizeStageKeyTest src.pool.tests.PhaseForMatchTest --settings=src.config.settings --verbosity=2</automated>
</verify>
<done>Todos os métodos das duas classes passam. `make test` (suite completa) também passa sem regressões.</done>
</task>

</tasks>

\<threat_model>

## Trust Boundaries

| Boundary                        | Descrição                                                                                                        |
| ------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Test runner → src.pool.services | Testes importam funções privadas (`_winner_from_score`); mudança de assinatura quebra os testes — sinal desejado |

## STRIDE Threat Register

| Threat ID        | Category               | Component                                       | Disposition | Mitigation Plan                                                                                                    |
| ---------------- | ---------------------- | ----------------------------------------------- | ----------- | ------------------------------------------------------------------------------------------------------------------ |
| T-01-02-01       | Tampering              | FIFA ID collision                               | mitigate    | Plano 02 usa apenas `SimpleTestCase` — sem ORM, sem fifa_id — colisão impossível                                   |
| T-01-02-02       | Repudiation            | SimpleTestCase acessando DB                     | mitigate    | Se qualquer import indireto tocar o ORM, Django levanta `AssertionError` imediatamente; falha ruidosa e detectável |
| T-01-02-03       | Information Disclosure | Importar `_winner_from_score` (símbolo privado) | accept      | É privado por convenção (prefixo `_`), não por proteção de segurança; acesso em testes é prática padrão Django     |
| \</threat_model> |                        |                                                 |             |                                                                                                                    |

<verification>
1. `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.ScoringWinnerFromScoreTest src.pool.tests.ScoringCalculateBetPointsTest src.pool.tests.NormalizeStageKeyTest src.pool.tests.PhaseForMatchTest --settings=src.config.settings --verbosity=2` — todos os testes passam
2. `make test` passa sem regressões nas classes existentes
3. Nenhum teste das novas classes acessa o banco de dados (verificado por `SimpleTestCase` que proíbe DB automaticamente)
</verification>

\<success_criteria>

- Todos os branches de `calculate_bet_points` e `normalize_stage_key` têm ao menos um teste direto
- Os cenários críticos estão cobertos: bet inativa, stage None, variantes PT/EN da API FIFA, empate 0-0, bônus inativo no mata-mata
- `make test` verde (sem regressões)
  \</success_criteria>

<output>
Após conclusão, crie `.planning/phases/01-qualidade-base/01-02-SUMMARY.md` com:
- Contagem de métodos de teste adicionados por classe
- Branches de `calculate_bet_points` e `normalize_stage_key` agora cobertos diretamente
- Qualquer comportamento inesperado encontrado durante a implementação
</output>
---
phase: "01-qualidade-base"
plan: "03"
type: execute
wave: 2
depends_on:
  - "02"
files_modified:
  - src/pool/tests.py
autonomous: true
requirements:
  - NFR-04
must_haves:
  truths:
    - "Os helpers puros de `context_builder.py` têm testes diretos: `_make_pairs`, `_infer_advancing_team`, `_infer_losing_team`, `_build_winners_map`, `_projection_is_stale_from_prefetched`, `_build_projected_groups_from_rows`, `_build_third_rows_from_rows`"
    - "A versão privada `_normalize_stage_key` de `context_builder.py` tem testes independentes dos testes de `rules.normalize_stage_key`"
    - "Nenhum teste deste plano usa banco de dados — todos são `SimpleTestCase`"
    - "`make test` passa sem regressões"
  artifacts:
    - path: "src/pool/tests.py"
      provides: "Classe `ContextBuilderPureHelpersTest` anexada ao arquivo"
      contains: "ContextBuilderPureHelpersTest"
  key_links:
    - from: "ContextBuilderPureHelpersTest"
      to: "src/pool/services/context_builder._infer_advancing_team"
      via: "import direto de símbolo privado; SimpleNamespace para match/bet/team"
      pattern: "from src.pool.services.context_builder import"
---

<objective>
Adicionar testes unitários para os helpers puros de `context_builder.py` que não acessam
banco de dados. Isso cobre a função `_normalize_stage_key` local (diferente da de `rules.py`)
e os demais helpers de inferência e construção de mapas.

Purpose: `context_builder.py` tem 549 linhas e zero testes diretos nos helpers. O plano
02 cobriu scoring e rules; este plano fecha a lacuna nos helpers de context_builder.
Depende do plano 02 apenas para garantir que o padrão `SimpleTestCase` + `SimpleNamespace`
já foi estabelecido no arquivo de testes (sem dependência de código — apenas convencional).

Output: Classe `ContextBuilderPureHelpersTest` anexada ao final de `src/pool/tests.py`.
</objective>

\<execution_context>
@/root/.claude/get-shit-done/workflows/execute-plan.md
\</execution_context>

<context>
@.planning/ROADMAP.md
@.planning/01/RESEARCH.md
@src/pool/tests.py
</context>

<interfaces>
<!-- Assinaturas exatas dos helpers a serem testados (lidas de src/pool/services/context_builder.py): -->

```python
# Linha 132
def _make_pairs(items):
    # Recebe lista, retorna lista de sublistas de tamanho 2 (última pode ter 1 elemento)
    return [items[index : index + 2] for index in range(0, len(items), 2)]

# Linha 136 — VERSÃO LOCAL, diferente de rules.normalize_stage_key:
# Retorna STAGE_SF, STAGE_QF, STAGE_R16, STAGE_R32, STAGE_THIRD, STAGE_FINAL ou ""
# NÃO retorna "GROUP" — não testa grupo nesta função local
def _normalize_stage_key(stage):
    # Constantes: STAGE_R32="R32", STAGE_R16="R16", STAGE_QF="QF",
    #             STAGE_SF="SF", STAGE_FINAL="FINAL", STAGE_THIRD="THIRD"

# Linha 90
def _infer_advancing_team(match, bet, home_team, away_team):
    # match: objeto com .winner_id, .winner
    # bet: objeto com .is_active, .winner_pred_id, .winner_pred,
    #      .home_score_pred, .away_score_pred (ou None)
    # home_team, away_team: objetos ou None
    # Retorna: team object ou None

# Linha 111
def _infer_losing_team(winner_team, home_team, away_team):
    # winner_team, home_team, away_team: objetos com atributo .id (ou None)
    # Retorna: team object ou None

# Linha 64
def _build_winners_map(matches, bets_by_match_id):
    # matches: lista de objetos com .id, .match_number, .home_team_id, .away_team_id,
    #          .home_team, .away_team, .winner_id, .winner
    # bets_by_match_id: dict {match_id: bet} onde bet tem .is_active, .winner_pred_id,
    #                   .winner_pred, .home_score_pred, .away_score_pred
    # Retorna: dict {match_number: team_object}

# Linha 374
def _projection_is_stale_from_prefetched(bets, projected_standings, projected_third_places):
    # bets: lista de objetos com .is_active, .match (.group_id ou None), .updated_at
    # projected_standings: lista de objetos com .updated_at
    # projected_third_places: lista de objetos com .updated_at
    # Retorna: bool

# Linha 399
def _build_projected_groups_from_rows(projected_standings):
    # projected_standings: lista de objetos com .group
    # Retorna: lista de dicts {"group": ..., "standings": lista}
    # ATENÇÃO: usa itertools.groupby — a lista DEVE estar ordenada por group para agrupar corretamente

# Linha 409
def _build_third_rows_from_rows(projected_third_places):
    # projected_third_places: lista de objetos com .group, .score, .position_global, .is_qualified
    # Retorna: lista de dicts {"group":..., "line":..., "score":..., "position_global":..., "is_qualified":...}
```

<!-- ATENÇÃO para _infer_losing_team: compara winner_team.id == home_team.id -->

<!-- Use SimpleNamespace com atributo .id explícito (ex: SimpleNamespace(id=1)) -->

</interfaces>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Testes para helpers puros de context_builder.py</name>
  <files>src/pool/tests.py</files>
  <behavior>
    _make_pairs:
    - lista vazia → []
    - lista com 1 elemento → [[elemento]]
    - lista com 2 elementos → [[a, b]]
    - lista com 3 elementos → [[a, b], [c]]
    - lista com 4 elementos → [[a, b], [c, d]]

```
_normalize_stage_key (versão local de context_builder — retorna constantes STAGE_*):
- stage=None → ""
- stage=SimpleNamespace(name="Semi-Final") → "SF"
- stage=SimpleNamespace(name="Semifinal") → "SF"
- stage=SimpleNamespace(name="Quarter-Final") → "QF"
- stage=SimpleNamespace(name="Round of 16") → "R16"
- stage=SimpleNamespace(name="Oitavas de Final") → "R16"
- stage=SimpleNamespace(name="Final") → "FINAL"
- stage=SimpleNamespace(name="Decisão 3o Lugar") → "THIRD"
- stage=SimpleNamespace(name="Mystery Stage") → ""
(NÃO testar "Group Stage" → não há branch GROUP nesta função local)

_infer_advancing_team:
- match.winner_id set AND match.winner set → retorna match.winner (resultado real prevalece)
- match.winner_id None, bet=None → None
- match.winner_id None, bet.is_active=False → None
- match.winner_id None, bet.is_active=True, bet.winner_pred_id set → retorna bet.winner_pred
- match.winner_id None, bet ativo, home_team=None → None
- match.winner_id None, bet ativo, home_pred > away_pred → retorna home_team
- match.winner_id None, bet ativo, away_pred > home_pred → retorna away_team
- match.winner_id None, bet ativo, home_pred == away_pred → None (empate, sem winner_pred)

_infer_losing_team:
- winner_team=None → None
- home_team=None → None
- away_team=None → None
- winner é home (winner.id == home.id) → retorna away_team
- winner é away (winner.id == away.id) → retorna home_team

_build_winners_map:
- bet ativo com winner_pred_id → usa winner_pred no mapa
- bet ativo com scores home > away (sem winner_pred_id) → usa match.home_team
- bet ativo com scores away > home (sem winner_pred_id) → usa match.away_team
- sem bet, match tem winner_id → usa match.winner
- sem bet, sem match.winner_id → não entra no mapa

_projection_is_stale_from_prefetched:
- sem bets ativos de grupo (todos inativos ou sem group_id) → False (sem baseline, não stale)
- standings vazio (bets ativos existem) → True
- third_places vazio (bets ativos existem) → True
- standings.updated_at < bet.updated_at → True
- third_places.updated_at < bet.updated_at → True
- standings e third_places mais recentes que bets → False

_build_projected_groups_from_rows:
- lista vazia → []
- 2 rows com mesmo group → [{"group": group, "standings": [row1, row2]}]
- 2 rows com groups diferentes → 2 dicts separados

_build_third_rows_from_rows:
- lista vazia → []
- 1 row → [{"group": row.group, "line": row, "score": row.score,
            "position_global": row.position_global, "is_qualified": row.is_qualified}]
```

</behavior>
  <action>
Anexe ao final de `src/pool/tests.py` (após as classes dos planos 02) a classe
`ContextBuilderPureHelpersTest` usando `SimpleTestCase`.

Importe os helpers privados diretamente:

```python
from src.pool.services.context_builder import (
    _make_pairs,
    _normalize_stage_key as _cb_normalize_stage_key,  # alias para diferenciar de rules.normalize_stage_key
    _infer_advancing_team,
    _infer_losing_team,
    _build_winners_map,
    _projection_is_stale_from_prefetched,
    _build_projected_groups_from_rows,
    _build_third_rows_from_rows,
)
```

Helpers de construção de objetos mock sugeridos (use SimpleNamespace):

```python
def _team(self, id):
    return SimpleNamespace(id=id)


def _bet(self, *, is_active=True, winner_pred_id=None, winner_pred=None, home_score_pred=None, away_score_pred=None):
    return SimpleNamespace(
        is_active=is_active,
        winner_pred_id=winner_pred_id,
        winner_pred=winner_pred,
        home_score_pred=home_score_pred,
        away_score_pred=away_score_pred,
    )


def _match_obj(
    self,
    *,
    id=1,
    match_number=1,
    winner_id=None,
    winner=None,
    home_team=None,
    away_team=None,
    home_team_id=None,
    away_team_id=None,
):
    return SimpleNamespace(
        id=id,
        match_number=match_number,
        winner_id=winner_id,
        winner=winner,
        home_team=home_team,
        away_team=away_team,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
    )
```

Para `_projection_is_stale_from_prefetched`, os objetos bet precisam de `updated_at`
e `match` com `group_id`. Use `datetime` para timestamps:

```python
from django.utils.timezone import now as tz_now
import datetime

# Exemplo:
t1 = tz_now() - datetime.timedelta(minutes=10)  # bet antiga
t2 = tz_now()  # standing recente
```

Para `_build_projected_groups_from_rows`, atenção: `itertools.groupby` agrupa apenas
linhas CONSECUTIVAS com o mesmo key. A lista de input DEVE estar ordenada por group
para o comportamento de múltiplos grupos funcionar. Use o mesmo group object para linhas
do mesmo grupo (não um novo SimpleNamespace por linha — `groupby` compara por identidade
do valor retornado pelo key function, que aqui é `row.group`):

```python
group_a = SimpleNamespace(name="A")
rows = [
    SimpleNamespace(group=group_a),
    SimpleNamespace(group=group_a),
]
result = _build_projected_groups_from_rows(rows)
# result[0]["group"] is group_a
# result[0]["standings"] == [rows[0], rows[1]]
```

Crie um método de teste por comportamento no `<behavior>`. Não agrupe múltiplos
comportamentos em um único método. Não use banco de dados.
</action>
<verify>
<automated>PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.ContextBuilderPureHelpersTest --settings=src.config.settings --verbosity=2</automated>
</verify>
<done>Todos os métodos da classe passam. `make test` passa sem regressões em nenhuma das classes existentes.</done>
</task>

</tasks>

\<threat_model>

## Trust Boundaries

| Boundary                                          | Descrição                                                                                           |
| ------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| Test runner → context_builder (símbolos privados) | Importar `_make_pairs`, `_normalize_stage_key` etc. — quebras de interface detectadas imediatamente |

## STRIDE Threat Register

| Threat ID        | Category          | Component                                                 | Disposition | Mitigation Plan                                                                                                                                                                     |
| ---------------- | ----------------- | --------------------------------------------------------- | ----------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| T-01-03-01       | Tampering         | FIFA ID collision                                         | mitigate    | Plano 03 usa apenas `SimpleTestCase` — sem ORM, sem fifa_id; colisão impossível                                                                                                     |
| T-01-03-02       | Repudiation       | `groupby` sem ordenação                                   | mitigate    | Documentado no `<action>`: o mock de input de `_build_projected_groups_from_rows` deve usar o mesmo objeto `group` e lista ordenada; caso contrário o teste falhará revelando o bug |
| T-01-03-03       | Denial of Service | Alias `_cb_normalize_stage_key` colide com nome de classe | accept      | Alias é escopo de módulo de teste; sem impacto em produção                                                                                                                          |
| \</threat_model> |                   |                                                           |             |                                                                                                                                                                                     |

<verification>
1. `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.ContextBuilderPureHelpersTest --settings=src.config.settings --verbosity=2` — todos os métodos passam
2. `make test` verde (sem regressões)
3. Nenhuma query DB executada (verificado implicitamente por `SimpleTestCase`)
4. A função `_normalize_stage_key` local de `context_builder.py` é testada de forma independente da `normalize_stage_key` de `rules.py`
</verification>

\<success_criteria>

- Todos os branches dos helpers puros de `context_builder.py` têm ao menos um teste direto
- A duplicação de `_normalize_stage_key` está documentada por testes independentes (um em rules, um em context_builder), tornando a divergência visível para refatoração futura na Phase 4
- `make test` verde
  \</success_criteria>

<output>
Após conclusão, crie `.planning/phases/01-qualidade-base/01-03-SUMMARY.md` com:
- Contagem de métodos de teste adicionados
- Helpers cobertos e helpers intencionalmente pulados (com justificativa)
- Qualquer divergência encontrada entre o comportamento real e o esperado no `<behavior>`
</output>
