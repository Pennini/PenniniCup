# Navbar: Palpites e Ranking como abas próprias

**Data:** 2026-06-10
**Branch base:** `fix/projection-upsert-race` (criar branch de feature a partir do estado atual)

## Objetivo

Reorganizar a navegação:

- A aba **Bolões** passa a servir **apenas para entrar em um bolão novo** (e ver seus bolões em modo leitura).
- **Palpites** e **Ranking** viram **duas novas abas** na navbar.
- As novas abas têm um **bloco compacto e visível** para selecionar o bolão que carrega a página — no mesmo padrão de Home e Regras.
- O bolão **default** é o **primeiro que o usuário entrou com token** (`PoolParticipant.joined_at` mais antigo).

## Estado atual (referência)

- Prefixo de include: `pool.urls` sob `/pools/`.
- Navbar (`src/templates/components/top_nav.html` desktop + `src/templates/components/bottom_nav.html` mobile): Home · Partidas · Regras · **Bolões** · Admin.
- **Bolões** (`pool:list` → `list.html`): card de inscrição por token **+** card "Abrir bolão" (select + botões Palpites/Ranking).
- **Palpites** = `pool:detail` (`/pools/<slug>/`), template `detail.html`, com sub-abas internas `bets` / `classification` / `knockout` (via `?tab=`).
- **Ranking** = `pool:ranking` (`/pools/<slug>/ranking/`, view `rankings.views.pool_ranking_dashboard`), template `rankings/pool_dashboard.html`.
- Home (`penninicup:index`) e Regras (`penninicup:rules`) já têm bloco seletor de bolão, mas o **default é alfabético** (`order_by("pool__name")` / `order_by("name")`).
- `PoolParticipant.joined_at = auto_now_add` existe → "primeiro entrado" = `order_by("joined_at")` e pegar o primeiro.

## Decisões (confirmadas com o usuário)

1. **Aba Bolões** = card de token + **lista de bolões do usuário em modo leitura** (nome + badge Apto/Pendente, sem botões de abrir). Remove o card "Abrir bolão".
1. **Default unificado** = primeiro bolão entrado (`joined_at`) em **Home, Regras, Palpites e Ranking**.
1. **URL das novas abas** = query-param `?pool=<slug>`. Mantém as views/rotas `pool:detail` e `pool:ranking` por baixo (back-compat e redirects pós-save).
1. **Regras** passa a listar **apenas os bolões do usuário** (participações), não mais todos os bolões ativos — para o default "primeiro entrado" fazer sentido.

## Mudanças

### A. Helper compartilhado de resolução

Criar um resolvedor único de participação selecionada (em `src/pool/services/` ou util compartilhado), com assinatura:

```
resolve_selected_participation(request, participations) -> (selected_participation, selected_slug)
```

- `participations`: queryset/lista de `PoolParticipant` ativos do usuário **ordenados por `joined_at`**.
- Lê `?pool=` (GET) — se presente e válido, seleciona; senão, primeiro da lista (primeiro entrado).
- Mensagem de aviso se `?pool=` não bate com nenhuma participação.

Substituir o `_resolve_selected_participation` atual de `penninicup/views.py` por este helper compartilhado e mudar as queries de `index` para `order_by("joined_at")`.

### B. Novas rotas (`src/pool/urls.py`)

Declarar **antes** de `path("<slug:slug>/", ...)` (literais não podem ser capturados como slug):

```text
path("palpites/", views.bets_tab, name="bets-tab"),
path("ranking/", views.ranking_tab, name="ranking-tab"),
```

URLs resultantes: `/pools/palpites/?pool=<slug>&tab=bets` e `/pools/ranking/?pool=<slug>`.

### C. Views (`src/pool/views.py` e `src/rankings/views.py`)

- Extrair de `pool_detail` um helper `_build_detail_render_context(request, pool, participant, active_tab, show_reprocess_notice)` retornando o `context` dict. `pool_detail` (rota slug) e `bets_tab` (rota query) usam o mesmo helper.
- `bets_tab(request)`:
  - participações ativas `order_by("joined_at")`.
  - `resolve_selected_participation`.
  - se nenhuma → renderiza estado vazio (mensagem "você ainda não participa..." + link para Bolões).
  - senão → resolve `active_tab` (`?tab=`, default `bets`), monta contexto compartilhado + `participations` + `selected_pool`, renderiza `detail.html`.
- Extrair de `pool_ranking_dashboard` um helper que monta o contexto a partir de `(pool, participant)`. `ranking_tab(request)` resolve participação e reusa o helper, renderizando `pool_dashboard.html` com `participations` + `selected_pool` no contexto.
- `ranking_tab` empty state idem.

### D. Partial seletor compartilhado

Criar `src/pool/templates/pool/partials/pool_selector.html`:

- Form GET compacto (baixa altura, visível): label "Bolão" + `<select name="pool" onchange="this.form.submit()">` iterando `participations`, marcando `selected_pool`.
- Hidden `tab` para preservar a sub-aba ao trocar de bolão (usado em Palpites; em Ranking o hidden é omitido/ignorado).
- `<noscript>` com botão submit de fallback.
- Inclui em `detail.html` (topo do conteúdo) e `pool_dashboard.html` (topo).

### E. Navbar (`top_nav.html` desktop + `bottom_nav.html` mobile)

Ordem: Home · Partidas · Regras · **Bolões** · **Palpites** · **Ranking** · Admin.

- **Bolões** ativo só em `pool:list`.
- **Palpites** (`{% url 'pool:bets-tab' %}`) ativo em `pool:bets-tab` + `pool:detail`.
- **Ranking** (`{% url 'pool:ranking-tab' %}`) ativo em `pool:ranking-tab` + `pool:ranking` + `rankings:pool-dashboard`.
- Botão "Salvar palpites" (desktop e mobile): condição passa a incluir `pool:bets-tab` além de `pool:detail` (form `pool-bets-form` vive em `detail.html`, reusado).
- Botão toggle-stars (superuser): condição passa a incluir `pool:ranking-tab`.
- Mobile sidebar: adicionar as duas novas entradas.

### F. Bolões page (`list.html`)

- Remover o card "Abrir bolão" (select + botões).
- Manter o card de inscrição por token.
- Adicionar lista read-only dos bolões do usuário: por `row` em `rows`, mostrar `pool.name` + badge `Apto`/`Pendente pagamento` (dados já em `rows`/`can_bet`), **sem** botões de abrir.

### G. Regras (`penninicup/views.py::rules` + `rules.html`)

- Trocar a fonte de `pools` (todos ativos) por **participações do usuário** `order_by("joined_at")`.
- Usar `resolve_selected_participation` para o default (primeiro entrado).
- Ajustar o template para iterar participações (o select já usa `pools`/`selected_pool`; adaptar para `participations`).
- Estado vazio: usuário sem bolão → mensagem já existente "Nenhum bolão ativo".

### H. Home (`penninicup/views.py::index`)

- `participations` query → `order_by("joined_at")`; default via helper compartilhado.

## Fora de escopo (YAGNI)

- Nenhuma mudança em scoring, projeção, pagamentos ou sync.
- Não remover as rotas `pool:detail` / `pool:ranking` (mantidas para back-compat e redirects pós-save).
- Sem mudança de estilo visual além do partial seletor e da lista read-only.

## Testes

- `pool/tests`: novas rotas `bets-tab` / `ranking-tab` resolvem default = primeiro entrado quando `?pool=` ausente; respeitam `?pool=` válido; aviso em `?pool=` inválido; estado vazio sem participações.
- Navbar: links presentes e classe ativa correta por view.
- Regras: lista só participações do usuário; default primeiro entrado (atualizar `test_rules_page_respects_selected_pool` se necessário).
- `list.html`: sem card "Abrir bolão"; com lista read-only.

## Riscos

- Colisão de URL: `palpites/` e `ranking/` devem vir antes de `<slug:slug>/` em `pool/urls.py`. Verificar ordem.
- Condições da navbar (`current_view`) — garantir que o botão "Salvar palpites" continue aparecendo na nova aba.
- `test_rules_page_respects_selected_pool` pode quebrar com a mudança de escopo de Regras; atualizar.
