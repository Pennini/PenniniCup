# Phase 2: Palpites Mobile-First - Research

**Researched:** 2026-05-06
**Domain:** Django template refactoring, TailwindCSS mobile-first layout, vanilla JS dirty state, Django messages toast
**Confidence:** HIGH

______________________________________________________________________

\<user_constraints>

## User Constraints (from CONTEXT.md)

### Locked Decisions

**Save Flow**

- D-01: Manter bulk save (`save_bets_bulk` POST) — sem auto-save AJAX por campo.
- D-02: Botão "Salvar palpites" aparece no **centro da top bar mobile** — visível/laranja apenas quando há dirty state. Quando limpo: oculto ou neutro.
- D-03: Manter lógica de `beforeunload` warning para campos sujos não salvos.
- D-04: Dirty state já existe no JS atual (`dirtyCards` set) — reutilizar para controlar visibilidade/cor do botão na top bar.

**Navegação e Agrupamento**

- D-05: Abaixo do toggle Grupos/Mata-mata/Classificação, adicionar segunda camada: barra de progresso + toggle de agrupamento lado a lado.
- D-06: Barra de progresso mostra `jogos palpitados / total` da fase ativa. "Palpitado" = palpite salvo no servidor.
- D-07: Toggle de agrupamento: "Por Data" | "Por Grupo" — dois modos de visualização.
- D-08: Padrão ao abrir: Por Data (jogos mais próximos primeiro).
- D-09: Agrupamento por data: blocos com cabeçalho de data, baseado nas datas reais das partidas.
- D-10: Agrupamento por grupo: blocos "Grupo A", "Grupo B", etc.
- D-11: Toggle de agrupamento não persiste entre sessões — reseta para Por Data a cada visita.
- D-12: Fase mata-mata: agrupamento sempre por fase (Oitavas/Quartas/Semi/Final) — toggle não se aplica.

**Layout de Cards Mobile**

- D-13: Layout centrado: `Time A  [00] : [00]  Time B` em uma linha. Legível em 320px.
- D-14: Cabeçalho do card: fase/grupo + número do jogo + badge de status — mesma linha superior.
- D-15: Card mata-mata com empate: segunda linha com select "Classificado: [Time ▾]" — comportamento JS mantido.
- D-16: Inputs de placar: tamanho mínimo 44px para toque confortável no mobile.
- D-17: Card do artilheiro: manter posição atual (topo da lista), sem alteração de layout.
- D-18: Badge de status (Aberto/Fechado) — manter pill atual, garantir legibilidade mobile.

**Feedback Visual**

- D-19: **Salvo:** toast "Palpites salvos!" por alguns segundos. Usar Django messages ou JS toast.
- D-20: **Pendente:** botão Salvar muda para laranja quando há dirty state.
- D-21: **Bloqueado:** inputs desabilitados + badge "Fechado" (pill border-red-400) — comportamento atual mantido no mobile.
- D-22: **Erro de save:** toast de erro global + badge vermelho inline no card que falhou.

### Claude's Discretion

- Implementação interna do toast (vanilla JS vs Django messages renderizados).
- Exata posição/HTML da top bar mobile para o botão Salvar contextual.
- Breakpoints Tailwind exatos para layout compacto dos cards.

### Deferred Ideas (OUT OF SCOPE)

- Nenhuma.
  \</user_constraints>

\<phase_requirements>

## Phase Requirements

| ID                     | Description                                                                                            | Research Support                                                                                               |
| ---------------------- | ------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------- |
| FR-01                  | Palpites Fase de Grupos — interface mobile-first para palpitar todos os jogos, com bloqueio automático | Cards mobile refatorados (D-13/D-14/D-16), agrupamento por data/grupo (D-07–D-10), feedback visual (D-19–D-22) |
| FR-03                  | Palpites Fase Mata-Mata — classificado + placar, agrupamento por fase                                  | Agrupamento mata-mata sempre por fase (D-12), card com select de classificado (D-15)                           |
| FR-09                  | Fluxo de Palpites Mobile-First — interface reformulada, feedback visual salvo/pendente/bloqueado       | Toda a fase: top bar contextual (D-02/D-04), barra de progresso (D-05/D-06), toast feedback (D-19/D-22)        |
| \</phase_requirements> |                                                                                                        |                                                                                                                |

______________________________________________________________________

## Summary

Esta fase é uma refatoração de template e JavaScript puro — **sem mudanças de modelo Django, sem novas rotas, sem migrações**. O backend (`save_bets_bulk`, `context_builder`) já entrega todos os dados necessários. Todo o trabalho acontece em `detail.html`, `top_nav.html`, e possivelmente um partial de card extraído.

O ponto central é: os dados (`group_rows`, `knockout_rows`) já chegam do servidor com `match.match_date_brasilia`, `match.group.name`, `row.locked`, `row.phase` — suficientes para implementar agrupamento por data e por grupo inteiramente no template Django sem nova query. O toggle de agrupamento (Por Data / Por Grupo) é lógica de apresentação JavaScript pura: esconder/mostrar seções pré-renderizadas.

O toast de save pode ser resolvido via Django messages já existentes no `base.html` — o `save_bets_bulk` já emite `messages.success` / `messages.error` no redirect. A única melhoria necessária é transformar a div estática de mensagens em um toast auto-dismiss com JS vanilla (já existente no projeto). Isso elimina a necessidade de qualquer nova infraestrutura.

**Primary recommendation:** Implementar todo o agrupamento como dois conjuntos de seções HTML pré-renderizadas (modo-data e modo-grupo) visíveis/ocultos via JS toggle. Reutilizar `dirtyCards` Set existente para controlar visibilidade e cor do botão Salvar na top bar.

______________________________________________________________________

## Architectural Responsibility Map

| Capability                                   | Primary Tier                    | Secondary Tier                                 | Rationale                                                                                               |
| -------------------------------------------- | ------------------------------- | ---------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| Agrupamento por data / por grupo             | Browser (JS toggle)             | Frontend Server (pré-renderiza ambos os modos) | Os dados já estão no contexto; mostrar/ocultar seções é mais simples que fetch AJAX                     |
| Barra de progresso (contagem de bets salvos) | Frontend Server (Django view)   | —                                              | Contagem depende de bets persistidos no servidor; precisa ser calculada no Python e enviada no contexto |
| Toast de sucesso/erro pós-save               | Browser (JS auto-dismiss)       | Frontend Server (Django messages)              | `save_bets_bulk` já emite messages; apenas adicionar auto-dismiss JS                                    |
| Botão Salvar contextual (dirty state)        | Browser (JS)                    | —                                              | `dirtyCards` Set já existe; apenas conectar ao botão da top bar                                         |
| Layout de cards mobile                       | Frontend Server (template HTML) | —                                              | Refatoração de markup + classes Tailwind                                                                |
| Badge de status (Aberto/Fechado)             | Frontend Server (template HTML) | —                                              | Já implementado via `row.locked`; garantir visibilidade mobile                                          |
| Inputs desabilitados (fase bloqueada)        | Frontend Server (template HTML) | —                                              | `{% if row.locked %}disabled{% endif %}` já existe                                                      |

______________________________________________________________________

## Standard Stack

### Core

| Library           | Version                            | Purpose                                                | Why Standard                                           |
| ----------------- | ---------------------------------- | ------------------------------------------------------ | ------------------------------------------------------ |
| TailwindCSS       | Já instalado via `django-tailwind` | Utility classes para layout mobile                     | Stack do projeto [VERIFIED: CLAUDE.md]                 |
| Django templates  | Django 6                           | Pré-renderização de HTML, agrupamento de dados         | Stack do projeto [VERIFIED: CLAUDE.md]                 |
| Vanilla JS (ES6+) | —                                  | Toggle de agrupamento, dirty state, toast auto-dismiss | Sem React/Alpine no projeto [VERIFIED: detalhe.html]   |
| Django messages   | Django 6 built-in                  | Toast de sucesso/erro pós-redirect                     | Já usado em `save_bets_bulk` [VERIFIED: pool/views.py] |

### Supporting

| Library      | Version       | Purpose                 | When to Use                                   |
| ------------ | ------------- | ----------------------- | --------------------------------------------- |
| Lucide icons | 0.468.0 (CDN) | Ícones (menu, check, x) | Já incluso no base.html [VERIFIED: base.html] |

### Alternatives Considered

| Instead of                            | Could Use                            | Tradeoff                                                                   |
| ------------------------------------- | ------------------------------------ | -------------------------------------------------------------------------- |
| Pré-renderizar dois modos no template | AJAX fetch ao mudar toggle           | Mais simples, sem roundtrip; menos JS; compatível com ATOMIC_REQUESTS      |
| Django messages auto-dismiss          | Biblioteca toast JS (Toastify, etc.) | Django messages já funciona; adicionar JS vanilla é suficiente             |
| Agrupamento Python no view            | Agrupamento JS no browser            | Python no view é mais testável; JS é mais simples se dados já estão no DOM |

**Installation:** Nenhuma dependência nova necessária. [VERIFIED: codebase]

______________________________________________________________________

## Architecture Patterns

### System Architecture Diagram

```
Requisição GET pool:detail
          │
          ▼
    pool_detail view
          │
          ├─► build_pool_participant_view_context()
          │        │
          │        ├─ group_rows: [{match, phase, home_team, away_team, bet, locked}]
          │        │    └─ match.match_date_brasilia  ← data para agrupamento
          │        │    └─ match.group.name           ← grupo para agrupamento
          │        │    └─ bet.is_active              ← para contagem de progresso
          │        └─ knockout_rows: [{...phase="KNOCKOUT"}]
          │             └─ match.stage.name           ← fase para agrupamento mata-mata
          │
          ▼
   detail.html renderiza:
     ├─ [Aba Palpites]
     │    ├─ Artilheiro card (topo, inalterado)
     │    ├─ Barra de progresso + Toggle Por Data / Por Grupo  ← NOVO
     │    │    └─ saved_bets_count / total_group_matches       ← NOVO contexto
     │    ├─ [data-group-mode="date"] seções por data          ← NOVO
     │    ├─ [data-group-mode="group"] seções por grupo        ← NOVO
     │    └─ Knockout rows (agrupados por fase)                ← refatorado
     │
     └─ JS inline:
          ├─ dirtyCards Set (já existe) → controla botão Salvar top bar
          ├─ Toggle de agrupamento (show/hide seções)          ← NOVO
          └─ Toast auto-dismiss de Django messages             ← NOVO

Requisição POST save_bets_bulk
          │
          ▼
    save_bets_bulk view
          ├─ Processa bets
          ├─ messages.success / messages.error
          └─ redirect → pool:detail
                    │
                    ▼
             detail.html → messages renderizados como toast via JS
```

### Recommended Project Structure

```
src/
├── pool/templates/pool/
│   ├── detail.html              # Arquivo principal — refatorado
│   └── partials/
│       └── bet_card.html        # (opcional) extrair card repetido group/knockout
└── templates/
    ├── components/
    │   └── top_nav.html         # Botão Salvar contextual — já existe, ajustar JS
    └── base.html                # Toast auto-dismiss JS — adicionar aqui ou em extra_scripts
```

### Pattern 1: Pré-renderizar dois modos de agrupamento

**What:** Renderizar dois conjuntos de seções HTML no template — um para "Por Data" e outro "Por Grupo" — e usar JS para alternar a visibilidade com `hidden`.

**When to use:** Quando os dados já estão disponíveis no contexto da view e não requerem novo fetch. Evita AJAX e mantém tudo em uma única renderização.

**Example:**

```html
{# detail.html — modo por data #}
<div id="group-mode-date">
  {% regroup group_rows by match.match_date_brasilia|date:"Y-m-d" as rows_by_date %}
  {% for date_group in rows_by_date %}
    <h3 class="text-xs uppercase tracking-wide text-neutral-400 mt-4 mb-2">
      {{ date_group.grouper|date:"d/m/Y" }}
    </h3>
    {% for row in date_group.list %}
      {% include "pool/partials/bet_card.html" %}
    {% endfor %}
  {% endfor %}
</div>

{# modo por grupo — hidden por padrão #}
<div id="group-mode-group" class="hidden">
  {% regroup group_rows by match.group.name as rows_by_group %}
  {% for group in rows_by_group %}
    <h3 ...>Grupo {{ group.grouper }}</h3>
    {% for row in group.list %}
      {% include "pool/partials/bet_card.html" %}
    {% endfor %}
  {% endfor %}
</div>
```

```javascript
// Toggle de agrupamento — vanilla JS
const btnDate  = document.getElementById('toggle-by-date');
const btnGroup = document.getElementById('toggle-by-group');
const modeDate  = document.getElementById('group-mode-date');
const modeGroup = document.getElementById('group-mode-group');

function setMode(mode) {
  if (mode === 'date') {
    modeDate.classList.remove('hidden');
    modeGroup.classList.add('hidden');
  } else {
    modeDate.classList.add('hidden');
    modeGroup.classList.remove('hidden');
  }
}
btnDate.addEventListener('click',  () => setMode('date'));
btnGroup.addEventListener('click', () => setMode('group'));
```

**Nota importante:** `{% regroup %}` do Django template requer que `group_rows` esteja **pré-ordenado** pela chave de agrupamento. O `context_builder` já ordena por `match_number, match_date_brasilia`. Para agrupamento por data, verificar se a ordem por data está garantida — se não, a view precisará ordenar `group_rows` por `match_date_brasilia` antes de enviar ao contexto. [VERIFIED: context_builder.py linha 428]

### Pattern 2: Botão Salvar contextual na top bar via JS

**What:** O botão Salvar já existe em `top_nav.html` (linhas 198-209). Ele está sempre visível quando `active_tab == 'bets'`. A mudança é: ocultar por padrão, mostrar com cor laranja apenas quando `dirtyCards.size > 0`.

**Example:**

```javascript
// No script de detail.html — estender o dirtyCards existente
const mobileTopBarBtn = document.querySelector('[data-topbar-save-btn]');

function syncTopBarButton() {
  if (!mobileTopBarBtn) return;
  if (hasPendingChanges()) {
    mobileTopBarBtn.classList.remove('hidden', 'bg-neutral-800', 'text-neutral-400');
    mobileTopBarBtn.classList.add('bg-orange-500', 'text-black');
  } else {
    mobileTopBarBtn.classList.add('hidden');
  }
}

// Chamar syncTopBarButton() após cada refreshDirtyState()
```

```html
{# top_nav.html — botão com data-attribute e estado inicial hidden #}
{% if current_view == 'pool:detail' and active_tab == 'bets' %}
<button
  data-topbar-save-btn
  type="submit"
  form="pool-bets-form"
  name="submit_action"
  value="save_all"
  class="hidden absolute left-1/2 -translate-x-1/2 px-3 py-1.5 rounded-md text-sm font-semibold"
  {% if not can_bet or group_locked and knockout_locked %}disabled{% endif %}
>
  Salvar
</button>
{% endif %}
```

### Pattern 3: Toast auto-dismiss para Django messages

**What:** `base.html` já renderiza Django messages como divs estáticas. Adicionar JS que auto-remove após N segundos.

**Example:**

```javascript
// base.html ou extra_scripts block — auto-dismiss de messages
(function () {
  const alerts = document.querySelectorAll('[data-django-message]');
  alerts.forEach(function (el) {
    setTimeout(function () {
      el.style.transition = 'opacity 0.4s';
      el.style.opacity = '0';
      setTimeout(function () { el.remove(); }, 400);
    }, 4000);
  });
})();
```

```html
{# base.html — adicionar data-attribute aos messages #}
{% for message in messages %}
<div
  data-django-message
  class="mb-2 rounded-md border px-4 py-3 text-sm ..."
  role="alert"
>
  {{ message }}
</div>
{% endfor %}
```

### Pattern 4: Barra de progresso — contagem no Python

**What:** `context_builder.py` já tem todos os bets. Adicionar `saved_bets_count` e `total_group_matches` ao contexto retornado.

**Example:**

```python
# context_builder.py — dentro de build_pool_participant_view_context()
total_group_matches = len(group_rows)
saved_bets_count = sum(
    1 for row in group_rows
    if row["bet"] and row["bet"].is_active
)
# ...
return {
    ...
    "saved_bets_count": saved_bets_count,
    "total_group_matches": total_group_matches,
}
```

```html
{# detail.html — barra de progresso #}
<div class="flex items-center gap-3">
  <div class="flex-1 h-1.5 rounded-full bg-neutral-800">
    <div
      class="h-1.5 rounded-full bg-orange-500"
      style="width: {% widthratio saved_bets_count total_group_matches 100 %}%"
    ></div>
  </div>
  <span class="text-xs text-neutral-400 whitespace-nowrap">
    {{ saved_bets_count }}/{{ total_group_matches }}
  </span>
</div>
```

### Pattern 5: Layout de card mobile compacto

**What:** Substituir o layout atual (dois cards separados para home/away) por uma linha única centrada: `[Bandeira] Time A  [00] : [00]  Time B [Bandeira]`.

**Key Tailwind classes:**

```html
<div class="flex items-center gap-2 w-full">
  <!-- Time casa -->
  <div class="flex items-center gap-1.5 flex-1 justify-end min-w-0">
    <span class="truncate text-sm font-medium text-right">{{ row.home_team.name|... }}</span>
    <span class="inline-flex h-5 w-5 shrink-0 ..."><img .../></span>
  </div>

  <!-- Placar -->
  <div class="flex items-center gap-1.5 shrink-0">
    <input type="number" min="0"
      name="match_{{ row.match.id }}_home_score_pred"
      class="w-11 h-11 rounded-md bg-black border border-neutral-700 text-center text-lg font-bold"
      {% if row.locked or not can_bet %}disabled{% endif %}
    />
    <span class="text-neutral-500 font-bold">:</span>
    <input type="number" min="0"
      name="match_{{ row.match.id }}_away_score_pred"
      class="w-11 h-11 rounded-md bg-black border border-neutral-700 text-center text-lg font-bold"
      {% if row.locked or not can_bet %}disabled{% endif %}
    />
  </div>

  <!-- Time visitante -->
  <div class="flex items-center gap-1.5 flex-1 justify-start min-w-0">
    <span class="inline-flex h-5 w-5 shrink-0 ..."><img .../></span>
    <span class="truncate text-sm font-medium">{{ row.away_team.name|... }}</span>
  </div>
</div>
```

`w-11` = 44px, satisfaz D-16 (toque mínimo 44px). Funciona em 320px com `text-sm` + `truncate`. [ASSUMED — breakpoints exatos dependem de validação visual no device]

### Anti-Patterns to Avoid

- **Agrupamento via AJAX:** Fazer fetch ao trocar o toggle adiciona latência, complexidade e risco de inconsistência com o formulário em andamento. Os dados já estão no HTML.
- **Persistir modo de agrupamento em localStorage:** D-11 proíbe persistência entre sessões. Não usar localStorage para o toggle.
- **Modificar `save_bets_bulk` para retornar JSON:** D-01 mantém o flow POST → redirect. Não converter para AJAX.
- **Auto-save individual por campo:** D-01 proíbe. Não adicionar `addEventListener('change', saveSingle)`.
- **Remover o `beforeunload` warning:** D-03 mantém. Não deletar o listener existente.

______________________________________________________________________

## Don't Hand-Roll

| Problem                         | Don't Build                           | Use Instead                                 | Why                                                                       |
| ------------------------------- | ------------------------------------- | ------------------------------------------- | ------------------------------------------------------------------------- |
| Agrupamento de listas por chave | Loop manual com dicionários em Python | `{% regroup %}` do Django template          | Built-in do Django, zero código extra [VERIFIED: docs.djangoproject.com]  |
| Proporção de progresso em %     | Cálculo JS                            | `{% widthratio %}` template tag             | Built-in do Django [VERIFIED: docs.djangoproject.com]                     |
| Toast de feedback               | Biblioteca JS externa                 | Django messages + JS auto-dismiss vanilla   | Infraestrutura já existente no projeto                                    |
| Botão submit fora do form       | Formulário duplicado                  | `form="pool-bets-form"` attribute no button | HTML5 nativo; já usado no top_nav.html [VERIFIED: top_nav.html linha 136] |

**Key insight:** Toda a lógica de agrupamento e contagem de progresso pode ser implementada com template tags nativas do Django (`{% regroup %}`, `{% widthratio %}`) e o contexto já disponível. Nenhuma nova view, endpoint, ou biblioteca é necessária.

______________________________________________________________________

## Runtime State Inventory

> Step 2.5: SKIPPED — esta fase é refatoração de template/JS/Python view context. Não é rename/refactor/migration. Nenhum dado armazenado é renomeado.

______________________________________________________________________

## Common Pitfalls

### Pitfall 1: `{% regroup %}` exige lista pré-ordenada

**What goes wrong:** `{% regroup group_rows by match.match_date_brasilia|date:"Y-m-d" %}` agrupa por blocos contíguos, não globalmente. Se `group_rows` não estiver ordenado por data, matches de mesma data mas posições não-contíguas gerarão múltiplos blocos com o mesmo cabeçalho.

**Why it happens:** `{% regroup %}` funciona como `itertools.groupby` — agrupa contiguamente, não ordena.

**How to avoid:** Garantir que `context_builder.py` ordena `group_rows` por `match_date_brasilia` quando enviado ao template. O `context_builder` atual ordena por `match_number, match_date_brasilia` — verificar se a ordenação resultante é por data primeiro. Alternativa: ordenar explicitamente no Python antes de fatiar em `group_rows`.

**Warning signs:** Múltiplos cabeçalhos de data com o mesmo dia no modo "Por Data".

### Pitfall 2: Botão Salvar na top bar — `form` attribute e disabled state

**What goes wrong:** O botão `<button form="pool-bets-form">` precisa que o form exista no DOM. Se o formulário não estiver na aba ativa (ex: tab=classification), o form não existe e o submit falha silenciosamente.

**Why it happens:** O botão Salvar na top bar já existe condicionalmente (`{% if active_tab == 'bets' %}`). A lógica de dirty state (JS) também só existirá quando o form existir.

**How to avoid:** Manter a condição `{% if active_tab == 'bets' %}` no template. O JS de dirty state já está dentro do bloco condicional. Não há problema — só adicionar `data-topbar-save-btn` ao botão existente.

**Warning signs:** `form.submit()` não chamado; botão presente mas form ausente.

### Pitfall 3: `can_bet` e `group_locked`/`knockout_locked` — interação de disabled

**What goes wrong:** O botão Salvar deve permanecer desabilitado se `not can_bet` OU se ambas as fases estiverem bloqueadas. O JS de dirty state não deve reativar um botão que o Django desabilitou.

**Why it happens:** `dirtyCards.size > 0` pode ser verdadeiro mesmo quando o form inteiro está desabilitado (usuário inspeciona DOM).

**How to avoid:** A verificação de disabled via atributo HTML (`{% if not can_bet or group_locked and knockout_locked %}disabled{% endif %}`) deve prevalecer. O JS de toggle de cor/visibilidade não deve remover o atributo `disabled` — apenas alterar classes visuais. O botão HTML disabled impede o submit independentemente de JS.

### Pitfall 4: Dupla renderização dos cards (modos data e grupo)

**What goes wrong:** Renderizar os mesmos cards duas vezes no HTML dobra os `name` attributes dos inputs (`match_123_home_score_pred` aparece duas vezes). O form submit envia ambos os valores, e o Django usa o primeiro ou cria lista.

**Why it happens:** Se usar `{% include %}` para os cards em ambos os modos, o HTML terá inputs duplicados com o mesmo `name`.

**How to avoid:** Renderizar os inputs apenas uma vez. Estratégia: no modo "grupo", mostrar os cards reais (com inputs). No modo "data", mostrar apenas cabeçalhos de data + referências visuais (sem inputs duplicados), OU usar `display:none` somente nos wrappers de agrupamento sem duplicar os inputs. **Abordagem recomendada:** renderizar todos os cards uma única vez em ordem canônica (por data, padrão), e construir os cabeçalhos de agrupamento como elementos separados que o JS reorganiza — ou melhor, renderizar os dois modos com os inputs em apenas um deles (o modo ativo padrão) e no modo alternativo mostrar somente cards de leitura sem inputs.

**Abordagem mais simples:** Renderizar uma única lista de cards e usar JS para inserir cabeçalhos de data/grupo dinamicamente com base em `data-match-date` e `data-match-group` attributes nos cards. Isso evita qualquer duplicação de inputs.

**Warning signs:** `request.POST.getlist('match_X_home_score_pred')` retorna lista em vez de string; saves inconsistentes.

### Pitfall 5: Toast via Django messages — visibilidade em tela cheia

**What goes wrong:** Django messages são renderizadas no `<main>` com `pt-20` (padding top para a top bar). Em mobile, se um toast fixo no topo é adicionado via JS, pode sobrepor o botão Salvar na top bar (z-index conflict) ou ficar atrás da top bar.

**Why it happens:** `top_nav` tem `z-50`; mensagens em `<main>` têm z-index padrão.

**How to avoid:** Auto-dismiss JS nas mensagens existentes (dentro do `<main>`) é suficiente — não reposicionar como toast fixo no topo. O posicionamento atual (dentro do conteúdo com padding top já aplicado) é correto. Alternativa: usar `fixed top-20 inset-x-4 z-40` para toast flutuante, garantindo que fique abaixo da top bar (z-50).

______________________________________________________________________

## Code Examples

### Barra de progresso — adição ao context_builder.py

```python
# Source: análise de context_builder.py — build_pool_participant_view_context()
# Adicionar logo antes do return final:
total_group_matches = len(group_rows)
saved_bets_count = sum(
    1 for row in group_rows
    if row["bet"] and row["bet"].is_active
)
# No return:
return {
    ...
    "saved_bets_count": saved_bets_count,
    "total_group_matches": total_group_matches,
}
```

### Dirty state conectado ao botão da top bar

```javascript
// Source: análise de detail.html JS block existente
// Estender a função refreshDirtyState existente:
function refreshDirtyState(card) {
    const initialPayload = initialPayloadByCard.get(card);
    const currentPayload = currentPayloadKey(card);
    if (initialPayload === currentPayload) {
        dirtyCards.delete(card);
    } else {
        dirtyCards.add(card);
    }
    syncTopBarButton();  // ← adicionar esta chamada
}

const mobileTopBarBtn = document.querySelector('[data-topbar-save-btn]');
function syncTopBarButton() {
    if (!mobileTopBarBtn) return;
    const pending = hasPendingChanges();
    mobileTopBarBtn.classList.toggle('hidden', !pending);
    mobileTopBarBtn.classList.toggle('bg-orange-500', pending);
    mobileTopBarBtn.classList.toggle('text-black', pending);
    mobileTopBarBtn.classList.toggle('bg-neutral-800', !pending);
    mobileTopBarBtn.classList.toggle('text-neutral-400', !pending);
}
// Chamar na inicialização também:
syncTopBarButton();
```

### Agrupamento por data com data-attribute (evita inputs duplicados)

```html
{# Renderizar cards com data-attributes para JS grouping #}
{% for row in group_rows %}
<article
  data-match-card
  data-phase="{{ row.phase }}"
  data-match-date="{{ row.match.match_date_brasilia|date:'Y-m-d' }}"
  data-match-date-label="{{ row.match.match_date_brasilia|date:'d/m/Y - l' }}"
  data-match-group="{{ row.match.group.name|default:'' }}"
  data-save-url="{% url 'pool:save-bet' pool.slug row.match.id %}"
  class="rounded-xl border border-neutral-700 p-4 bg-neutral-900"
>
  ...
</article>
{% endfor %}
```

```javascript
// JS: inserir cabeçalhos dinamicamente — sem duplicar inputs
function applyGrouping(mode) {
    // Remover cabeçalhos anteriores
    document.querySelectorAll('[data-grouping-header]').forEach(el => el.remove());

    const cards = document.querySelectorAll('[data-match-card][data-phase="GROUP"]');
    let lastKey = null;

    cards.forEach(card => {
        const key = mode === 'date'
            ? card.dataset.matchDate
            : card.dataset.matchGroup;
        const label = mode === 'date'
            ? card.dataset.matchDateLabel
            : 'Grupo ' + card.dataset.matchGroup;

        if (key !== lastKey) {
            const header = document.createElement('h3');
            header.setAttribute('data-grouping-header', '');
            header.className = 'text-xs uppercase tracking-wide text-neutral-400 mt-4 mb-2 px-1';
            header.textContent = label;
            card.parentNode.insertBefore(header, card);
            lastKey = key;
        }
    });
}
// Inicializar com modo 'date'
applyGrouping('date');
```

**Nota:** Esta abordagem com JS inserindo cabeçalhos evita completamente a duplicação de inputs (Pitfall 4). \[ASSUMED — validar que `group_rows` está ordenado por data antes de aplicar\]

### Toast auto-dismiss

```javascript
// Adicionar ao base.html no bloco extra_scripts ou inline após messages
(function () {
    document.querySelectorAll('[data-django-message]').forEach(function (el) {
        setTimeout(function () {
            el.style.transition = 'opacity 0.5s ease';
            el.style.opacity = '0';
            setTimeout(function () { el.remove(); }, 500);
        }, 4000);
    });
})();
```

______________________________________________________________________

## State of the Art

| Old Approach                                              | Current Approach                                                    | When Changed | Impact                                                            |
| --------------------------------------------------------- | ------------------------------------------------------------------- | ------------ | ----------------------------------------------------------------- |
| Layout atual: dois cards separados para home/away em grid | Layout compacto mobile: linha única `Time A [00]:[00] Time B`       | Esta fase    | Reduz altura por card em ~50%; caberá mais jogos na viewport      |
| Botão Salvar sempre visível no top bar                    | Botão Salvar contextual (laranja quando dirty, hidden quando clean) | Esta fase    | Feedback imediato de estado pendente                              |
| Mensagens estáticas no topo da página                     | Toast auto-dismiss (4s)                                             | Esta fase    | UX mais limpa; não exige scroll para ver feedback                 |
| Nenhum agrupamento (lista flat)                           | Agrupamento por data (padrão) ou por grupo                          | Esta fase    | Orientação temporal durante a Copa (jogos mais próximos primeiro) |

______________________________________________________________________

## Assumptions Log

| #   | Claim                                                                                                    | Section                           | Risk if Wrong                                                                                                    |
| --- | -------------------------------------------------------------------------------------------------------- | --------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| A1  | `group_rows` está ordenado por `match_date_brasilia` de forma a produzir agrupamentos contíguos por data | Architecture Patterns / Pitfall 1 | Cabeçalhos de data repetidos; correção: adicionar `.order_by('match_date_brasilia')` na query do context_builder |
| A2  | `w-11` (44px) para inputs de placar funciona em telas de 320px com nomes de time truncados               | Pattern 5 / Card layout           | Campo pode overflow; correção: reduzir para `w-10` (40px) + ajustar flexbox                                      |
| A3  | JS inserindo cabeçalhos via `insertBefore` mantém ordem correta quando `group_rows` já está ordenado     | Code Examples                     | Cabeçalhos em posição errada; correção: usar `{% regroup %}` com lista ordenada e marcação HTML                  |

**Se A1 for falso:** Adicionar `group_rows.sort(key=lambda r: r["match"].match_date_brasilia)` ao `context_builder.py` antes de retornar — ou garantir na query que `match_date_brasilia` tem precedência.

______________________________________________________________________

## Open Questions

1. **Ordenação de `group_rows` por data**

   - What we know: `context_builder.py` ordena matches por `match_number, match_date_brasilia`. Em geral na Copa do Mundo, `match_number` e `match_date_brasilia` são correlacionados mas não idênticos (jogos do mesmo dia têm números sequenciais, mas jogos de dias diferentes nem sempre).
   - What's unclear: Se a ordem atual de `group_rows` garante que todos os matches de uma data ficam contíguos, sem interleaving de datas.
   - Recommendation: Verificar o banco de dados dev ou adicionar ordenação explícita por `match_date_brasilia` no context_builder antes do agrupamento.

1. **`active_tab` no contexto de `top_nav.html`**

   - What we know: `top_nav.html` usa `active_tab` como variável de contexto (linha 130: `{% if current_view == 'pool:detail' and active_tab == 'bets' %}`). Esta variável é passada pela `pool_detail` view.
   - What's unclear: Se `active_tab` está disponível no contexto de `top_nav.html` quando incluso via `{% include %}` no `base.html`. Django templates herdam o contexto pai nos includes por padrão.
   - Recommendation: Verificar que `pool_detail` view passa `active_tab` no contexto — confirmado na linha 165 de `views.py`. Funciona.

______________________________________________________________________

## Environment Availability

> Step 2.6: SKIPPED — esta fase é puramente de template/JS/Python. Nenhuma ferramenta externa, serviço, runtime adicional, ou CLI além do stack existente (Python 3.12, Django 6, TailwindCSS) é necessário.

______________________________________________________________________

## Validation Architecture

> `nyquist_validation: false` em `.planning/config.json` — seção omitida conforme instrução.

______________________________________________________________________

## Security Domain

### Applicable ASVS Categories

| ASVS Category         | Applies          | Standard Control                                            |
| --------------------- | ---------------- | ----------------------------------------------------------- |
| V2 Authentication     | não              | —                                                           |
| V3 Session Management | não              | —                                                           |
| V4 Access Control     | não (inalterado) | `@login_required` já em pool_detail e save_bets_bulk        |
| V5 Input Validation   | não (inalterado) | `PoolBetForm` + `full_clean()` já validam; sem novos inputs |
| V6 Cryptography       | não              | —                                                           |

**Nota de segurança:** Esta fase não altera nenhum endpoint backend. Todo o trabalho é template + JS. A superfície de ataque permanece idêntica. O botão Salvar da top bar usa `form="pool-bets-form"` submit — o mesmo POST para `save_bets_bulk` já protegido por `@login_required`, `@ratelimit`, e `ATOMIC_REQUESTS`. [VERIFIED: views.py, top_nav.html]

______________________________________________________________________

## Sources

### Primary (HIGH confidence)

- `src/pool/templates/pool/detail.html` — template atual completo, JS inline, estrutura de cards [VERIFIED: leitura direta]
- `src/templates/components/top_nav.html` — botão Salvar mobile existente, z-index, classes [VERIFIED: leitura direta]
- `src/pool/services/context_builder.py` — `build_pool_participant_view_context`, campos disponíveis no contexto [VERIFIED: leitura direta]
- `src/pool/views.py` — `save_bets_bulk`, `pool_detail`, Django messages emitidas [VERIFIED: leitura direta]
- `src/templates/base.html` — estrutura messages rendering, z-index top_nav [VERIFIED: leitura direta]
- `.planning/codebase/CONVENTIONS.md` — padrões de código Python/template do projeto [VERIFIED: leitura direta]
- `CLAUDE.md` — stack, comandos de teste, regras de qualidade [VERIFIED: leitura direta]
- `.planning/config.json` — `nyquist_validation: false` [VERIFIED: leitura direta]

### Secondary (MEDIUM confidence)

- Django template docs: `{% regroup %}` tag comportamento com listas ordenadas [ASSUMED — knowledge training, Django 6 backward compatible]

______________________________________________________________________

## Metadata

**Confidence breakdown:**

- Template/JS changes: HIGH — código fonte lido diretamente; comportamento compreendido
- Contexto disponível no view: HIGH — `context_builder.py` lido integralmente
- Ordenação de `group_rows` por data: MEDIUM — requer verificação do banco dev (A1)
- Breakpoints de card em 320px: MEDIUM — requer validação visual (A2)

**Research date:** 2026-05-06
**Valid until:** 2026-06-01 (estável — sem dependências externas mutáveis)
