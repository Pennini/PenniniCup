# Phase 2: Palpites Mobile-First - Pattern Map

**Mapped:** 2026-05-06
**Files analyzed:** 4 files to be modified (no new files)
**Analogs found:** 4 / 4

______________________________________________________________________

## File Classification

| Modified File                           | Role                 | Data Flow        | Closest Analog    | Match Quality |
| --------------------------------------- | -------------------- | ---------------- | ----------------- | ------------- |
| `src/pool/templates/pool/detail.html`   | component (template) | request-response | itself (refactor) | self          |
| `src/templates/components/top_nav.html` | component (template) | request-response | itself (extend)   | self          |
| `src/templates/base.html`               | layout (template)    | request-response | itself (extend)   | self          |
| `src/pool/services/context_builder.py`  | service              | CRUD             | itself (extend)   | self          |

> This phase is a pure refactoring with no new files. All 4 targets are existing files being extended.
> Pattern assignments below show: (1) exact patterns from the existing code to preserve, and
> (2) the concrete insertion points and snippets to add.

______________________________________________________________________

## Pattern Assignments

### `src/pool/templates/pool/detail.html` (component, request-response)

**Current state read:** Lines 1–497 (full file — 497 lines)

#### Pattern A: Existing card structure to preserve

The group and knockout card markup (lines 68–204 and 232–368) shares this wrapper:

```html
{# lines 69–70 — group card opening with data-attributes already present #}
<article class="rounded-xl border border-neutral-700 p-4 bg-neutral-900"
         data-match-card
         data-phase="{{ row.phase }}"
         data-save-url="{% url 'pool:save-bet' pool.slug row.match.id %}">
```

New data-attributes to ADD to this opening tag (needed for JS grouping):

```html
data-match-date="{{ row.match.match_date_brasilia|date:'Y-m-d' }}"
data-match-date-label="{{ row.match.match_date_brasilia|date:'d/m - l' }}"
data-match-group="{{ row.match.group.name|default:'' }}"
```

#### Pattern B: Existing score input pattern to refactor into mobile-first layout

Current layout uses two separate sub-cards in a 2-column grid (lines 119–181). Replace with single-line:

```html
{# CURRENT (lines 119–121) — two-column grid to replace: #}
<div class="mt-4 rounded-lg border border-neutral-800 bg-neutral-950/40 p-3 space-y-3">
    <div class="grid gap-3 min-[900px]:grid-cols-2">
        <div class="rounded-md border border-neutral-800 bg-neutral-950 p-3">
```

New single-line layout to use (copy `name`, `value`, `data-score-home/away`, `disabled` logic verbatim from lines 138–148 and 169–179):

```html
<div class="mt-3 flex items-center gap-2 w-full">
  <!-- Time casa -->
  <div class="flex items-center gap-1.5 flex-1 justify-end min-w-0">
    {% if row.home_team and row.home_team.flag_image_url %}
    <span class="inline-flex h-5 w-5 overflow-hidden rounded-full ring-1 ring-neutral-600 bg-neutral-800 shrink-0">
      <img src="{{ row.home_team.flag_image_url }}" alt="Bandeira {{ row.home_team.name }}"
           class="h-full w-full object-cover" loading="lazy" />
    </span>
    {% endif %}
    <span class="truncate text-sm font-medium text-right">
      {{ row.home_team.name|default:row.match.home_placeholder|default:"A definir" }}
    </span>
  </div>

  <!-- Placar -->
  <div class="flex items-center gap-1 shrink-0">
    <input type="number" min="0"
           name="match_{{ row.match.id }}_home_score_pred"
           value="{% if row.bet %}{{ row.bet.home_score_pred }}{% endif %}"
           data-score-home
           class="w-11 h-11 rounded-md bg-black border border-neutral-700 text-center text-lg font-bold"
           placeholder="0"
           aria-label="Palpite de gols do time da casa"
           {% if row.locked or not can_bet %}disabled{% endif %} />
    <span class="text-neutral-500 font-bold px-0.5">:</span>
    <input type="number" min="0"
           name="match_{{ row.match.id }}_away_score_pred"
           value="{% if row.bet %}{{ row.bet.away_score_pred }}{% endif %}"
           data-score-away
           class="w-11 h-11 rounded-md bg-black border border-neutral-700 text-center text-lg font-bold"
           placeholder="0"
           aria-label="Palpite de gols do time visitante"
           {% if row.locked or not can_bet %}disabled{% endif %} />
  </div>

  <!-- Time visitante -->
  <div class="flex items-center gap-1.5 flex-1 justify-start min-w-0">
    <span class="truncate text-sm font-medium">
      {{ row.away_team.name|default:row.match.away_placeholder|default:"A definir" }}
    </span>
    {% if row.away_team and row.away_team.flag_image_url %}
    <span class="inline-flex h-5 w-5 overflow-hidden rounded-full ring-1 ring-neutral-600 bg-neutral-800 shrink-0">
      <img src="{{ row.away_team.flag_image_url }}" alt="Bandeira {{ row.away_team.name }}"
           class="h-full w-full object-cover" loading="lazy" />
    </span>
    {% endif %}
  </div>
</div>
```

#### Pattern C: Card header — preserve badge pill verbatim

Lines 43–47 (artilheiro) and lines 111–115 (match cards) show the pill pattern. Keep exactly:

```html
{% if row.locked %}
<span class="text-xs px-2 py-1 rounded-full border border-red-400 text-red-300">Fechado</span>
{% else %}
<span class="text-xs px-2 py-1 rounded-full border border-emerald-400 text-emerald-300">Aberto</span>
{% endif %}
```

New card header format: combine phase/group label + match number + badge in one flex row (replaces the current `<div>` block at lines 70–115):

```html
<div class="flex items-center justify-between gap-2 mb-3">
  <div class="flex items-center gap-2 min-w-0">
    <p class="text-xs uppercase tracking-wide text-neutral-400 truncate">
      {% if row.phase == "GROUP" %}Grupo {{ row.match.group.name|default:"-" }}
      {% else %}Mata-mata - {{ row.match.stage.name|default:"-" }}{% endif %}
    </p>
    <span class="text-xs text-neutral-600">#{{ row.match.match_number }}</span>
  </div>
  {% if row.locked %}
  <span class="text-xs px-2 py-1 rounded-full border border-red-400 text-red-300 shrink-0">Fechado</span>
  {% else %}
  <span class="text-xs px-2 py-1 rounded-full border border-emerald-400 text-emerald-300 shrink-0">Aberto</span>
  {% endif %}
</div>
```

#### Pattern D: Knockout winner select — preserve verbatim

Lines 183–201 and 347–365 contain the `[data-winner-wrapper]` + `[data-winner-select]` markup. The JS at lines 416–436 depends on these exact `data-*` attributes. Do not rename them.

```html
{# line 184 — keep these exact data-attributes: #}
<div data-winner-wrapper class="mt-2">
    <select
        name="match_{{ row.match.id }}_winner_pred"
        data-winner-select
        data-base-disabled="{% if row.locked or not can_bet %}1{% else %}0{% endif %}"
        class="w-full rounded-md bg-black border border-neutral-700 px-3 py-2.5"
        {% if row.locked or not can_bet %}disabled{% endif %}
    >
```

#### Pattern E: Progress bar + grouping toggle — insertion point

Insert below the `{% include "components/pool_bet_toggle_three_tabs.html" %}` line (line 26) and BEFORE the `{% if active_tab == "classification" %}` check (line 28). Only show when `active_tab == "bets"`:

```html
{% if active_tab == "bets" %}
<div class="flex items-center gap-3">
  <!-- Progress bar -->
  <div class="flex-1 h-1.5 rounded-full bg-neutral-800">
    <div
      class="h-1.5 rounded-full bg-orange-500 transition-all"
      style="width: {% widthratio saved_bets_count total_group_matches 100 %}%"
    ></div>
  </div>
  <span class="text-xs text-neutral-400 whitespace-nowrap">
    {{ saved_bets_count }}/{{ total_group_matches }}
  </span>

  <!-- Grouping toggle (group phase only) -->
  <div class="flex rounded-lg border border-neutral-700 overflow-hidden shrink-0" id="grouping-toggle">
    <button id="toggle-by-date"
            class="px-2.5 py-1 text-xs font-semibold bg-neutral-800 text-orange-200 transition-colors"
            aria-pressed="true">Por Data</button>
    <button id="toggle-by-group"
            class="px-2.5 py-1 text-xs font-semibold text-neutral-400 hover:text-neutral-200 transition-colors"
            aria-pressed="false">Por Grupo</button>
  </div>
</div>
{% endif %}
```

#### Pattern F: Grouping JS — insertion point in existing `<script>` block

The existing `<script>` block (lines 371–494) starts with `(function () { const form = ...`. Insert the grouping and top-bar sync code INSIDE this same IIFE, after the `dirtyCards` declarations and before the `hasPendingChanges` function.

```javascript
// --- TOP BAR SYNC (insert after line 381: let isSubmitting = false;) ---
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

// --- GROUPING (insert after syncTopBarButton definition) ---
function applyGrouping(mode) {
    document.querySelectorAll('[data-grouping-header]').forEach(function (el) { el.remove(); });
    const groupCards = document.querySelectorAll('[data-match-card][data-phase="GROUP"]');
    let lastKey = null;
    groupCards.forEach(function (card) {
        const key   = mode === 'date' ? card.dataset.matchDate  : card.dataset.matchGroup;
        const label = mode === 'date' ? card.dataset.matchDateLabel : 'Grupo ' + card.dataset.matchGroup;
        if (key !== lastKey) {
            const header = document.createElement('h3');
            header.setAttribute('data-grouping-header', '');
            header.className = 'text-xs uppercase tracking-wide text-neutral-400 mt-4 mb-2 px-1';
            header.textContent = label;
            card.parentNode.insertBefore(header, card);
            lastKey = key;
        }
    });

    const btnDate  = document.getElementById('toggle-by-date');
    const btnGroup = document.getElementById('toggle-by-group');
    if (btnDate && btnGroup) {
        btnDate.classList.toggle('bg-neutral-800',   mode === 'date');
        btnDate.classList.toggle('text-orange-200',  mode === 'date');
        btnDate.classList.toggle('text-neutral-400', mode !== 'date');
        btnGroup.classList.toggle('bg-neutral-800',   mode === 'group');
        btnGroup.classList.toggle('text-orange-200',  mode === 'group');
        btnGroup.classList.toggle('text-neutral-400', mode !== 'group');
        btnDate.setAttribute('aria-pressed',  mode === 'date'  ? 'true' : 'false');
        btnGroup.setAttribute('aria-pressed', mode === 'group' ? 'true' : 'false');
    }
}
```

The call to `syncTopBarButton()` must be added at the end of the existing `refreshDirtyState` function (lines 405–414):

```javascript
// Existing refreshDirtyState (lines 405–414) — add syncTopBarButton() call:
function refreshDirtyState(card) {
    const initialPayload = initialPayloadByCard.get(card);
    const currentPayload = currentPayloadKey(card);
    if (initialPayload === currentPayload) {
        dirtyCards.delete(card);
    } else {
        dirtyCards.add(card);
    }
    syncTopBarButton();  // <-- ADD THIS LINE
}
```

Wire grouping toggle buttons and initialize (insert before the closing `})();` of the IIFE):

```javascript
// Init grouping and toggle buttons (insert before closing })(); )
const btnDate  = document.getElementById('toggle-by-date');
const btnGroup = document.getElementById('toggle-by-group');
if (btnDate)  btnDate.addEventListener('click',  function () { applyGrouping('date'); });
if (btnGroup) btnGroup.addEventListener('click', function () { applyGrouping('group'); });
applyGrouping('date');  // default: by date (D-08)
syncTopBarButton();     // initialize button state
```

______________________________________________________________________

### `src/templates/components/top_nav.html` (component, request-response)

**Current state read:** Lines 1–211 (full file — 211 lines)

#### Pattern A: Desktop save button — preserve verbatim (lines 130–141)

```html
{% if current_view == 'pool:detail' and active_tab == 'bets' %}
<button
    type="submit"
    form="pool-bets-form"
    name="submit_action"
    value="save_all"
    class="px-4 py-2 rounded-md bg-orange-500 text-black font-semibold disabled:opacity-50 disabled:cursor-not-allowed"
    {% if not can_bet or group_locked and knockout_locked %}disabled{% endif %}
>
    Salvar palpites
</button>
{% endif %}
```

#### Pattern B: Mobile save button — modify existing (lines 198–209)

Current button is always-visible orange. Change to: hidden by default, no `bg-orange-500` class (JS will add it via `syncTopBarButton`), add `data-topbar-save-btn` attribute:

```html
{# CURRENT lines 198–209 — REPLACE with: #}
{% if current_view == 'pool:detail' and active_tab == 'bets' %}
<button
    type="submit"
    form="pool-bets-form"
    name="submit_action"
    value="save_all"
    data-topbar-save-btn
    class="hidden absolute left-1/2 -translate-x-1/2 px-3 py-1.5 rounded-md text-sm font-semibold disabled:opacity-50 disabled:cursor-not-allowed bg-neutral-800 text-neutral-400"
    {% if not can_bet or group_locked and knockout_locked %}disabled{% endif %}
>
    Salvar
</button>
{% endif %}
```

Key changes vs current:

- Add `data-topbar-save-btn` — JS selector target
- Add `hidden` class — JS removes it when dirty
- Change `bg-orange-500 text-black` to `bg-neutral-800 text-neutral-400` — JS toggles between these states

______________________________________________________________________

### `src/templates/base.html` (layout, request-response)

**Current state read:** Lines 1–46 (full file — 46 lines)

#### Pattern A: Messages rendering — current verbatim (lines 23–33)

```html
{% if messages %}
<div class="mb-4">
    {% for message in messages %}
    <div
        class="mb-2 rounded-md border px-4 py-3 text-sm {% if 'success' in message.tags %}border-green-500/30 bg-green-500/10 text-green-300{% elif 'warning' in message.tags %}border-amber-500/30 bg-amber-500/10 text-amber-300{% elif 'error' in message.tags %}border-red-500/30 bg-red-500/10 text-red-300{% else %}border-blue-500/30 bg-blue-500/10 text-blue-300{% endif %}"
        role="alert"
    >
        {{ message }}
    </div>
    {% endfor %}
</div>
{% endif %}
```

#### Pattern B: Add `data-django-message` attribute and auto-dismiss JS

Modify each message `<div>` (line 27) to add the data-attribute:

```html
{# Change line 27 — add data-django-message: #}
<div
    data-django-message
    class="mb-2 rounded-md border px-4 py-3 text-sm ..."
    role="alert"
>
```

Add auto-dismiss script AFTER the `{% block extra_scripts %}{% endblock %}` block (line 39) and BEFORE the `lucide.createIcons()` call (line 41):

```javascript
<script>
(function () {
    document.querySelectorAll('[data-django-message]').forEach(function (el) {
        setTimeout(function () {
            el.style.transition = 'opacity 0.5s ease';
            el.style.opacity = '0';
            setTimeout(function () { el.remove(); }, 500);
        }, 4000);
    });
})();
</script>
```

**Placement note:** Must be after `{% block extra_scripts %}` so page-level scripts (like `detail.html`'s IIFE) run first. Place the auto-dismiss before `lucide.createIcons()` so it runs on DOMContentLoaded order.

______________________________________________________________________

### `src/pool/services/context_builder.py` (service, CRUD)

**Current state read:** Lines 422–549 (function body)

#### Pattern A: Return statement — current verbatim (lines 537–549)

```python
return {
    "match_rows": match_rows,
    "group_rows": group_rows,
    "knockout_rows": knockout_rows,
    "projected_groups": projected_groups,
    "can_bet": participant_can_bet,
    "group_locked": pool.is_phase_locked(PHASE_GROUP),
    "knockout_locked": pool.is_phase_locked(PHASE_KNOCKOUT),
    "projection_pending": has_pending_projection_recalc(participant),
    "top_scorer_options": _top_scorer_options_payload_for_pool(pool),
    "page_mode": "result",
    **projected_knockout,
}
```

#### Pattern B: Add progress counts before return

Insert immediately before the `return {` statement (line 537):

```python
# Progress counters for phase 2 progress bar (D-06)
total_group_matches = len(group_rows)
saved_bets_count = sum(1 for row in group_rows if row["bet"] and row["bet"].is_active)
```

Then add to the return dict:

```python
return {
    ...existing keys...,
    "saved_bets_count": saved_bets_count,
    "total_group_matches": total_group_matches,
}
```

#### Pattern C: Ordering guarantee for grouping (Pitfall 1 from RESEARCH.md)

Current query (lines 425–429) orders by `match_number, match_date_brasilia`. Verify that `group_rows` as built produces contiguous date blocks. If not, add explicit sort after lines 514–517 where `group_rows.append(row)` occurs:

```python
# After the match loop ends (after line 517) and before knockout processing:
group_rows.sort(key=lambda r: r["match"].match_date_brasilia)
```

This is a defensive sort — safe to add regardless because `group_rows` is a list built in the same function.

______________________________________________________________________

## Shared Patterns

### Dark Theme Classes

**Source:** `src/pool/templates/pool/detail.html` throughout
**Apply to:** All new HTML elements in any modified template

```
bg-neutral-900   — card backgrounds
bg-neutral-800   — secondary/inner backgrounds
bg-black         — input backgrounds
border-neutral-700  — card borders
border-neutral-800  — inner element borders
text-neutral-400    — muted labels
text-neutral-500    — very muted text
```

### Action Color

**Source:** `src/pool/templates/pool/detail.html` line 21 (`bg-orange-500`) and `top_nav.html` line 136 (`bg-orange-500`)
**Apply to:** Save button dirty state, progress bar fill, active toggle button

```
bg-orange-500     — primary action background
text-orange-200   — active state text on dark
text-black        — text on orange background
```

### Disabled Pattern

**Source:** `src/pool/templates/pool/detail.html` lines 147, 178, 190
**Apply to:** Any new inputs or buttons that can be locked

```html
{% if row.locked or not can_bet %}disabled{% endif %}
```

### Conditional Button (form attribute)

**Source:** `src/templates/components/top_nav.html` lines 131–141 (desktop) and 198–209 (mobile)
**Apply to:** Any submit button outside the `<form>` tag

```html
type="submit"
form="pool-bets-form"
name="submit_action"
value="save_all"
```

### Messages Pattern

**Source:** `src/pool/views.py` lines 296, 300, 386–387, 415, 424, 427, 431
**Apply to:** No changes needed in views — `save_bets_bulk` already emits all needed message levels (success, error, info, warning).

______________________________________________________________________

## No Analog Found

None. All patterns have direct source in the existing codebase.

______________________________________________________________________

## Metadata

**Analog search scope:** `src/pool/templates/`, `src/templates/components/`, `src/pool/services/`, `src/pool/views.py`, `src/templates/base.html`
**Files scanned:** 7 files read directly
**Pattern extraction date:** 2026-05-06
