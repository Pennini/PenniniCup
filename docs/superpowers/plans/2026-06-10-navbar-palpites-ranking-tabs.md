# Navbar Palpites & Ranking Tabs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tornar **Bolões** uma aba só de entrada/listagem, e criar abas **Palpites** e **Ranking** na navbar, cada uma com seletor compacto de bolão (default = primeiro entrado por token).

**Architecture:** Reusa as views existentes `pool_detail` e `pool_ranking_dashboard` extraindo a montagem de contexto em helpers compartilhados. Duas novas views slug-less (`bets_tab`, `ranking_tab`) resolvem a participação default (`joined_at` mais antigo) ou via `?pool=<slug>`, e renderizam os mesmos templates `detail.html` / `pool_dashboard.html` acrescidos de um partial seletor. Home e Regras passam a usar o mesmo resolvedor e o mesmo default.

**Tech Stack:** Django 6, templates Django, TailwindCSS (CSS já compilado — sem build novo), testes `django.test.TestCase`.

**Spec:** `docs/superpowers/specs/2026-06-10-navbar-palpites-ranking-tabs-design.md`

**Comando de teste (use sempre este):**
`make test-single path=src/pool/tests/<arquivo>.py`
Ou módulo específico: `make test-single path=src/pool/tests/test_navigation_tabs.py`

______________________________________________________________________

## File Structure

- **Create** `src/pool/services/participants.py` — `resolve_selected_participation(request, participations)`. Única fonte de verdade da resolução default/`?pool=`.
- **Create** `src/pool/templates/pool/partials/pool_selector.html` — partial seletor compacto (GET form). Reusado em detail + ranking.
- **Create** `src/pool/templates/pool/no_pool_selected.html` — estado vazio das abas Palpites/Ranking.
- **Create** `src/pool/tests/test_navigation_tabs.py` — testes das novas rotas/views.
- **Modify** `src/pool/views.py` — extrair `_build_bets_context`; adicionar `bets_tab`, `ranking_tab`.
- **Modify** `src/rankings/views.py` — extrair `build_ranking_dashboard_context`.
- **Modify** `src/pool/urls.py` — rotas `palpites/` e `ranking/` antes de `<slug:slug>/`.
- **Modify** `src/pool/templates/pool/detail.html` — incluir seletor quando `participations`.
- **Modify** `src/rankings/templates/rankings/pool_dashboard.html` — incluir seletor quando `participations`.
- **Modify** `src/templates/components/top_nav.html` — links Palpites/Ranking + condições de active/botões.
- **Modify** `src/templates/components/bottom_nav.html` — entradas mobile Palpites/Ranking.
- **Modify** `src/pool/templates/pool/list.html` — remover card "Abrir bolão", adicionar lista read-only.
- **Modify** `src/penninicup/views.py` — `index` e `rules` usam resolver + `joined_at`; deletar `_resolve_selected_participation` local.
- **Modify** `src/penninicup/templates/penninicup/rules.html` — select itera `participations`.
- **Modify** `src/penninicup/tests.py` — `RulesPageTest` agora cria participações do owner.

Sem mudanças de modelo → **sem migration**.

______________________________________________________________________

## Task 1: Resolvedor compartilhado de participação

**Files:**

- Create: `src/pool/services/participants.py`

- Test: `src/pool/tests/test_navigation_tabs.py`

- [ ] **Step 1: Criar o helper**

`src/pool/services/participants.py`:

```python
from django.contrib import messages


def resolve_selected_participation(request, participations):
    """Resolve a participação selecionada via ?pool=<slug> ou o primeiro entrado.

    `participations` deve vir ordenado por joined_at (primeiro entrado primeiro).
    Retorna (participation_or_None, selected_slug).
    """
    selected_slug = (request.GET.get("pool") or "").strip()
    selected = None
    if selected_slug:
        selected = next((p for p in participations if p.pool.slug == selected_slug), None)
        if selected is None:
            messages.warning(
                request,
                "Bolão selecionado não encontrado entre suas participações ativas.",
            )
    if selected is None and participations:
        selected = participations[0]
    return selected, selected_slug
```

- [ ] **Step 2: Commit**

```bash
git add src/pool/services/participants.py
git commit -m "feat(pool): resolvedor compartilhado de participação selecionada"
```

______________________________________________________________________

## Task 2: Extrair contexto de ranking (rankings/views.py)

**Files:**

- Modify: `src/rankings/views.py`

- [ ] **Step 1: Extrair `build_ranking_dashboard_context` e reusar em `pool_ranking_dashboard`**

Substituir a função `pool_ranking_dashboard` (linhas atuais ~9-66) por:

```python
def build_ranking_dashboard_context(*, pool, participant):
    leaderboard_rows = build_pool_leaderboard(pool=pool)
    total_participants = len(leaderboard_rows)

    current_row = next(
        (row for row in leaderboard_rows if row.participant.id == participant.id),
        None,
    )
    leader_points = leaderboard_rows[0].participant.total_points if leaderboard_rows else 0
    points_gap = max(leader_points - participant.total_points, 0)

    podium_rows = leaderboard_rows[:3]
    podium_prizes = [
        "Premiação 1º lugar",
        "Premiação 2º lugar",
        "Premiação 3º lugar",
    ]
    podium_cards = []
    for row in podium_rows:
        prize_text = (
            podium_prizes[row.position - 1] if row.position <= len(podium_prizes) else "Premiação não definida"
        )
        podium_cards.append(
            {
                "position": row.position,
                "username": row.participant.user.username,
                "points": row.participant.total_points,
                "prize": prize_text,
                "prize_amount": (
                    pool.first_place_amount
                    if row.position == 1
                    else pool.second_place_amount
                    if row.position == 2
                    else pool.third_place_amount
                ),
            }
        )

    return {
        "pool": pool,
        "leaderboard_rows": leaderboard_rows,
        "podium_cards": podium_cards,
        "current_participant": participant,
        "current_position": current_row.position if current_row else None,
        "total_participants": total_participants,
        "leader_points": leader_points,
        "points_gap": points_gap,
        "total_prize_amount": pool.total_prize_amount,
        "first_place_amount": pool.first_place_amount,
        "second_place_amount": pool.second_place_amount,
        "third_place_amount": pool.third_place_amount,
    }


@login_required
def pool_ranking_dashboard(request, slug):
    pool = get_object_or_404(Pool.objects.select_related("season"), slug=slug, is_active=True)
    pool.refresh_prize_distribution()
    current_participant = get_object_or_404(PoolParticipant, pool=pool, user=request.user, is_active=True)
    context = build_ranking_dashboard_context(pool=pool, participant=current_participant)
    return render(request, "rankings/pool_dashboard.html", context)
```

(`toggle_supporter_stars` permanece inalterada.)

- [ ] **Step 2: Rodar testes de ranking existentes (não pode quebrar)**

Run: `make test-single path=src/rankings/tests.py`
Expected: PASS (mesmo comportamento, só refatorado). Se não houver `src/rankings/tests.py`, rode `make test-single path=src/pool/tests/test_pool.py` como smoke.

- [ ] **Step 3: Commit**

```bash
git add src/rankings/views.py
git commit -m "refactor(rankings): extrai build_ranking_dashboard_context"
```

______________________________________________________________________

## Task 3: Novas views bets_tab / ranking_tab + extração de contexto de palpites

**Files:**

- Modify: `src/pool/views.py`

- Modify: `src/pool/urls.py`

- Create: `src/pool/templates/pool/no_pool_selected.html`

- Test: `src/pool/tests/test_navigation_tabs.py`

- [ ] **Step 1: Escrever os testes (falhando)**

`src/pool/tests/test_navigation_tabs.py`:

```python
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from src.football.models import Competition, Season
from src.pool.models import Pool, PoolParticipant

User = get_user_model()


class NavigationTabsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="navuser", email="nav@example.com", password="123456Aa!")
        self.client.force_login(self.user)
        competition = Competition.objects.create(fifa_id=700, name="Copa Nav")
        self.season = Season.objects.create(
            fifa_id=700,
            competition=competition,
            name="Temporada Nav",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        # "Zebra" entrou primeiro, "Alpha" depois → default deve ser Zebra (não alfabético).
        self.pool_zebra = Pool.objects.create(
            name="Zebra", slug="zebra", season=self.season, created_by=self.user, requires_payment=False
        )
        self.pool_alpha = Pool.objects.create(
            name="Alpha", slug="alpha", season=self.season, created_by=self.user, requires_payment=False
        )
        self.part_zebra = PoolParticipant.objects.create(pool=self.pool_zebra, user=self.user, is_active=True)
        self.part_alpha = PoolParticipant.objects.create(pool=self.pool_alpha, user=self.user, is_active=True)

    def test_bets_tab_defaults_to_first_joined(self):
        response = self.client.get(reverse("pool:bets-tab"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_pool"].slug, "zebra")

    def test_bets_tab_respects_pool_param(self):
        response = self.client.get(reverse("pool:bets-tab"), data={"pool": "alpha"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_pool"].slug, "alpha")

    def test_bets_tab_invalid_pool_falls_back_to_default(self):
        response = self.client.get(reverse("pool:bets-tab"), data={"pool": "naoexiste"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_pool"].slug, "zebra")

    def test_bets_tab_empty_state(self):
        other = User.objects.create_user(username="lonely", email="lonely@example.com", password="123456Aa!")
        self.client.force_login(other)
        response = self.client.get(reverse("pool:bets-tab"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ainda não participa")

    def test_ranking_tab_defaults_to_first_joined(self):
        response = self.client.get(reverse("pool:ranking-tab"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_pool"].slug, "zebra")

    def test_navbar_has_palpites_and_ranking_links(self):
        response = self.client.get(reverse("pool:bets-tab"))
        self.assertContains(response, reverse("pool:bets-tab"))
        self.assertContains(response, reverse("pool:ranking-tab"))
```

- [ ] **Step 2: Rodar os testes — devem falhar**

Run: `make test-single path=src/pool/tests/test_navigation_tabs.py`
Expected: FAIL com `NoReverseMatch` para `pool:bets-tab` (rotas ainda não existem).

- [ ] **Step 3: Criar o template de estado vazio**

`src/pool/templates/pool/no_pool_selected.html`:

```html
{% extends "base.html" %}

{% block title %}{% if page_kind == "ranking" %}Ranking{% else %}Palpites{% endif %}{% endblock %}

{% block content %}
<section class="mx-auto w-full max-w-2xl py-10">
    <article class="rounded-2xl border border-blue-500/20 bg-blue-500/10 p-6 text-center shadow-lg shadow-black/15">
        <h1 class="text-xl font-semibold text-blue-100">Você ainda não participa de nenhum bolão</h1>
        <p class="mt-2 text-sm text-blue-100/85">
            Entre em um bolão com seu token de convite para ver
            {% if page_kind == "ranking" %}o ranking{% else %}seus palpites{% endif %}.
        </p>
        <a href="{% url 'pool:list' %}" class="mt-4 inline-flex rounded-lg bg-orange-500 px-4 py-2 text-sm font-semibold text-black hover:bg-orange-400 transition-colors">
            Ir para Bolões
        </a>
    </article>
</section>
{% endblock %}
```

- [ ] **Step 4: Refatorar `pool_detail` e adicionar `bets_tab` / `ranking_tab`**

Em `src/pool/views.py`, adicionar o import no topo (junto aos demais imports de services):

```python
from src.pool.services.participants import resolve_selected_participation
```

Substituir a função `pool_detail` atual por (extrai `_build_bets_context`):

```python
def _build_bets_context(request, pool, participant, active_tab):
    pool_context = build_pool_participant_view_context(pool=pool, participant=participant, ensure_bets=True)
    show_reprocess_notice = (request.GET.get("reprocess") or "").strip() == "1"
    return {
        "pool": pool,
        "participant": participant,
        "active_tab": active_tab,
        "show_reprocess_notice": show_reprocess_notice,
        **pool_context,
    }


@login_required
def pool_detail(request, slug):
    pool = get_object_or_404(Pool.objects.select_related("season"), slug=slug, is_active=True)
    participant = get_object_or_404(PoolParticipant, pool=pool, user=request.user)
    active_tab = (request.GET.get("tab") or "bets").strip()
    if active_tab not in ("bets", "classification", "knockout"):
        return redirect(f"{request.path}?tab=bets")
    context = _build_bets_context(request, pool, participant, active_tab)
    return render(request, "pool/detail.html", context)


def _active_participations(user):
    return list(
        PoolParticipant.objects.filter(user=user, is_active=True)
        .select_related("pool", "pool__season")
        .order_by("joined_at")
    )


@login_required
def bets_tab(request):
    participations = _active_participations(request.user)
    selected, _ = resolve_selected_participation(request, participations)
    if selected is None:
        return render(request, "pool/no_pool_selected.html", {"page_kind": "bets"})

    pool = selected.pool
    active_tab = (request.GET.get("tab") or "bets").strip()
    if active_tab not in ("bets", "classification", "knockout"):
        active_tab = "bets"
    context = _build_bets_context(request, pool, selected, active_tab)
    context["participations"] = participations
    context["selected_pool"] = pool
    return render(request, "pool/detail.html", context)


@login_required
def ranking_tab(request):
    from src.rankings.views import build_ranking_dashboard_context

    participations = _active_participations(request.user)
    selected, _ = resolve_selected_participation(request, participations)
    if selected is None:
        return render(request, "pool/no_pool_selected.html", {"page_kind": "ranking"})

    pool = selected.pool
    pool.refresh_prize_distribution()
    context = build_ranking_dashboard_context(pool=pool, participant=selected)
    context["participations"] = participations
    context["selected_pool"] = pool
    return render(request, "rankings/pool_dashboard.html", context)
```

(O import de `build_ranking_dashboard_context` é local à função para evitar import circular entre `pool.views` e `rankings.views`.)

- [ ] **Step 5: Adicionar as rotas em `src/pool/urls.py`**

Inserir as duas rotas **logo após** `path("open/", views.open_pool, name="open")` e **antes** de `path("<slug:slug>/ranking/", ...)`:

```python
    path("palpites/", views.bets_tab, name="bets-tab"),
    path("ranking/", views.ranking_tab, name="ranking-tab"),
```

Arquivo final esperado:

```python
urlpatterns = [
    path("", views.pool_list, name="list"),
    path("join-by-token/", views.join_pool_by_token, name="join-by-token"),
    path("open/", views.open_pool, name="open"),
    path("palpites/", views.bets_tab, name="bets-tab"),
    path("ranking/", views.ranking_tab, name="ranking-tab"),
    path("<slug:slug>/ranking/", pool_ranking_dashboard, name="ranking"),
    path("<slug:slug>/", views.pool_detail, name="detail"),
    path("<slug:slug>/join/", views.join_pool, name="join"),
    path("<slug:slug>/bet/<int:match_id>/", views.save_bet, name="save-bet"),
    path("<slug:slug>/bets/save/", views.save_bets_bulk, name="save-bets-bulk"),
    path("<slug:slug>/projection-status/", views.projection_status, name="projection-status"),
    path("<slug:slug>/knockout-cards/", views.knockout_cards_partial, name="knockout-cards"),
]
```

- [ ] **Step 6: Rodar os testes — devem passar**

Run: `make test-single path=src/pool/tests/test_navigation_tabs.py`
Expected: PASS (6 testes). `test_navbar_has_palpites_and_ranking_links` já passa porque as rotas existem e o base template renderiza o navbar (links adicionados na Task 5 reforçam, mas `reverse` já resolve).

> Nota: se `test_navbar_has_palpites_and_ranking_links` falhar por o link ainda não estar no HTML, ele passará após a Task 5. Pode deixar falhando só esse e revisitar, OU mover esse assert para depois da Task 5. Os demais 5 testes devem passar agora.

- [ ] **Step 7: Commit**

```bash
git add src/pool/views.py src/pool/urls.py src/pool/templates/pool/no_pool_selected.html src/pool/tests/test_navigation_tabs.py
git commit -m "feat(pool): abas Palpites e Ranking com seletor de bolão (default primeiro entrado)"
```

______________________________________________________________________

## Task 4: Partial seletor + inclusão nos templates

**Files:**

- Create: `src/pool/templates/pool/partials/pool_selector.html`

- Modify: `src/pool/templates/pool/detail.html`

- Modify: `src/rankings/templates/rankings/pool_dashboard.html`

- [ ] **Step 1: Criar o partial**

`src/pool/templates/pool/partials/pool_selector.html`:

```html
{% comment %}Inputs: participations, selected_pool, optional active_tab{% endcomment %}
<form method="get" class="flex items-center gap-3 rounded-xl border border-neutral-700 bg-neutral-950/60 px-3 py-2">
    <label for="pool-selector" class="text-xs uppercase tracking-[0.16em] text-neutral-400 shrink-0">Bolão</label>
    <select
        id="pool-selector"
        name="pool"
        class="flex-1 rounded-lg border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-orange-500/40"
        onchange="this.form.submit()"
    >
        {% for participation in participations %}
        <option value="{{ participation.pool.slug }}" {% if selected_pool and participation.pool.id == selected_pool.id %}selected{% endif %}>
            {{ participation.pool.name }}
        </option>
        {% endfor %}
    </select>
    {% if active_tab %}<input type="hidden" name="tab" value="{{ active_tab }}" />{% endif %}
    <noscript>
        <button type="submit" class="px-3 py-2 rounded-lg bg-orange-500 text-black text-sm font-semibold">Ir</button>
    </noscript>
</form>
```

- [ ] **Step 2: Incluir em `detail.html`**

Em `src/pool/templates/pool/detail.html`, logo após `</header>` (atual linha 13) e antes do bloco `{% if not can_bet %}`, inserir:

```html
    {% if participations %}
    {% include "pool/partials/pool_selector.html" with active_tab=active_tab %}
    {% endif %}
```

(O guard `{% if participations %}` garante que a rota slug `pool:detail` — que não passa `participations` — não renderiza o seletor.)

- [ ] **Step 3: Incluir em `pool_dashboard.html`**

Em `src/rankings/templates/rankings/pool_dashboard.html`, logo após `</header>` (atual linha 16), inserir:

```html
    {% if participations %}
    {% include "pool/partials/pool_selector.html" %}
    {% endif %}
```

- [ ] **Step 4: Rodar testes das abas**

Run: `make test-single path=src/pool/tests/test_navigation_tabs.py`
Expected: PASS. Verifica que o render com seletor não quebra.

- [ ] **Step 5: Commit**

```bash
git add src/pool/templates/pool/partials/pool_selector.html src/pool/templates/pool/detail.html src/rankings/templates/rankings/pool_dashboard.html
git commit -m "feat(pool): partial seletor de bolão em Palpites e Ranking"
```

______________________________________________________________________

## Task 5: Navbar — links Palpites/Ranking e condições

**Files:**

- Modify: `src/templates/components/top_nav.html`

- Modify: `src/templates/components/bottom_nav.html`

- [ ] **Step 1: Desktop — trocar o link "Bolões" e adicionar Palpites + Ranking**

Em `src/templates/components/top_nav.html`, substituir o bloco do link Bolões (atual `<a href="{% url 'pool:list' %}" ...>Bolões</a>`, linhas ~90-103) por estes três links:

```html
            <a
                href="{% url 'pool:list' %}"
                class="nav-link flex items-center h-16 px-3 border-b-2 -mb-[1px] transition-colors {% if current_view == 'pool:list' %}nav-link-active{% endif %}"
            >
                Bolões
            </a>
            <a
                href="{% url 'pool:bets-tab' %}"
                class="nav-link flex items-center h-16 px-3 border-b-2 -mb-[1px] transition-colors {% if current_view == 'pool:bets-tab' or current_view == 'pool:detail' %}nav-link-active{% endif %}"
            >
                Palpites
            </a>
            <a
                href="{% url 'pool:ranking-tab' %}"
                class="nav-link flex items-center h-16 px-3 border-b-2 -mb-[1px] transition-colors {% if current_view == 'pool:ranking-tab' or current_view == 'pool:ranking' or current_view == 'rankings:pool-dashboard' %}nav-link-active{% endif %}"
            >
                Ranking
            </a>
```

(Os atributos `aria-current` malformados do markup antigo — `{% if current_view="" ... %}` — são descartados nesta troca; eles já não funcionavam.)

- [ ] **Step 2: Desktop — botão "Salvar palpites" também na nova aba**

No mesmo arquivo, na condição do botão "Salvar palpites" (atual `{% if current_view == 'pool:detail' and active_tab == 'bets' %}`, linha ~130), trocar por:

```html
        {% if current_view == 'pool:detail' or current_view == 'pool:bets-tab' %}{% if active_tab == 'bets' %}
```

e fechar o `{% endif %}` extra correspondente. Forma final do trecho:

```html
        {% if current_view == 'pool:detail' or current_view == 'pool:bets-tab' %}{% if active_tab == 'bets' %}
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
        {% endif %}{% endif %}
```

- [ ] **Step 3: Desktop — toggle-stars também na nova aba ranking**

Trocar a condição do bloco toggle-stars (atual, linha ~142):

```html
        {% if user.is_superuser and current_view == 'rankings:pool-dashboard' or user.is_superuser and current_view == 'pool:ranking' %}
```

por:

```html
        {% if user.is_superuser and current_view == 'rankings:pool-dashboard' or user.is_superuser and current_view == 'pool:ranking' or user.is_superuser and current_view == 'pool:ranking-tab' %}
```

Fazer a mesma troca no bloco toggle-stars da navbar mobile no mesmo arquivo (linha ~222).

E na condição do botão "Salvar" mobile (linha ~209):

```html
    {% if current_view == 'pool:detail' or current_view == 'pool:bets-tab' %}{% if active_tab == 'bets' %}
```

fechando com `{% endif %}{% endif %}` no `{% endif %}` correspondente (linha ~221).

- [ ] **Step 4: Mobile sidebar — adicionar Palpites e Ranking**

Em `src/templates/components/bottom_nav.html`, trocar o link Bolões (atual linhas ~48-54) por estes três:

```html
            <a
                href="{% url 'pool:list' %}"
                class="flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm {% if current_view == 'pool:list' %}bg-orange-500/15 text-orange-200 border border-orange-500/30{% else %}text-neutral-300 border border-transparent hover:border-neutral-700 hover:bg-neutral-900{% endif %}"
            >
                <i data-lucide="trophy" class="w-4 h-4"></i>
                <span>Bolões</span>
            </a>

            <a
                href="{% url 'pool:bets-tab' %}"
                class="flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm {% if current_view == 'pool:bets-tab' or current_view == 'pool:detail' %}bg-orange-500/15 text-orange-200 border border-orange-500/30{% else %}text-neutral-300 border border-transparent hover:border-neutral-700 hover:bg-neutral-900{% endif %}"
            >
                <i data-lucide="pencil" class="w-4 h-4"></i>
                <span>Palpites</span>
            </a>

            <a
                href="{% url 'pool:ranking-tab' %}"
                class="flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm {% if current_view == 'pool:ranking-tab' or current_view == 'pool:ranking' or current_view == 'rankings:pool-dashboard' %}bg-orange-500/15 text-orange-200 border border-orange-500/30{% else %}text-neutral-300 border border-transparent hover:border-neutral-700 hover:bg-neutral-900{% endif %}"
            >
                <i data-lucide="bar-chart-3" class="w-4 h-4"></i>
                <span>Ranking</span>
            </a>
```

- [ ] **Step 5: Rodar os testes das abas (navbar link assertion)**

Run: `make test-single path=src/pool/tests/test_navigation_tabs.py`
Expected: PASS — incluindo `test_navbar_has_palpites_and_ranking_links`.

- [ ] **Step 6: Commit**

```bash
git add src/templates/components/top_nav.html src/templates/components/bottom_nav.html
git commit -m "feat(nav): abas Palpites e Ranking na navbar desktop e mobile"
```

______________________________________________________________________

## Task 6: Bolões page — remover "Abrir bolão", adicionar lista read-only

**Files:**

- Modify: `src/pool/templates/pool/list.html`

- [ ] **Step 1: Substituir o segundo `<article>` (card "Abrir bolão")**

Em `src/pool/templates/pool/list.html`, substituir todo o segundo `<article>` (atual linhas 40-85, "Abrir bolão") por:

```html
        <article class="rounded-2xl border border-neutral-800 bg-neutral-900/90 p-5 sm:p-6 shadow-lg shadow-black/15">
            <div class="flex items-start gap-3">
                <span class="mt-0.5 inline-flex h-9 w-9 items-center justify-center rounded-lg bg-blue-500/10 text-blue-400 border border-blue-500/20">
                    <i data-lucide="trophy" class="h-4 w-4"></i>
                </span>
                <div>
                    <h2 class="text-lg sm:text-xl font-semibold">Meus bolões</h2>
                    <p class="text-sm text-neutral-400 mt-1">Use as abas Palpites e Ranking para abrir cada bolão.</p>
                </div>
            </div>

            {% if rows %}
            <ul class="mt-4 space-y-2">
                {% for row in rows %}
                <li class="flex items-center justify-between rounded-lg border border-neutral-700 bg-neutral-950/70 px-4 py-3">
                    <span class="font-medium">{{ row.pool.name }}</span>
                    {% if row.can_bet %}
                    <span class="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-1 text-xs font-semibold text-emerald-200">Apto</span>
                    {% else %}
                    <span class="rounded-full border border-amber-500/30 bg-amber-500/10 px-2.5 py-1 text-xs font-semibold text-amber-200">Pendente pagamento</span>
                    {% endif %}
                </li>
                {% endfor %}
            </ul>
            {% else %}
            <p class="mt-4 rounded-lg border border-blue-500/20 bg-blue-500/10 px-3 py-2 text-sm text-blue-300">Você ainda não está inscrito em nenhum bolão.</p>
            {% endif %}
        </article>
```

(`rows` e `row.can_bet` já são fornecidos por `pool_list` em `src/pool/views.py` — sem mudança de view.)

- [ ] **Step 2: Smoke test da página Bolões**

Run: `make test-single path=src/pool/tests/test_pool.py`
Expected: PASS (nenhum teste depende do card removido; se algum referenciar "Abrir bolão"/`pool:open`, atualizar — ver self-review).

- [ ] **Step 3: Commit**

```bash
git add src/pool/templates/pool/list.html
git commit -m "feat(pool): Bolões vira entrada por token + lista read-only"
```

______________________________________________________________________

## Task 7: Home (index) — default primeiro entrado

**Files:**

- Modify: `src/penninicup/views.py`

- [ ] **Step 1: Importar o resolvedor compartilhado**

No topo de `src/penninicup/views.py`, adicionar:

```python
from src.pool.services.participants import resolve_selected_participation
```

- [ ] **Step 2: `index` ordena por `joined_at` e usa o resolvedor**

Na função `index`, trocar a query `participations` (atual `order_by("pool__name")`) por `order_by("joined_at")`:

```python
    participations = list(
        PoolParticipant.objects.filter(user=request.user, is_active=True)
        .select_related("pool", "pool__season")
        .order_by("joined_at")
    )

    selected_participation, selected_slug = resolve_selected_participation(request, participations)
```

- [ ] **Step 3: Remover o `_resolve_selected_participation` local**

Deletar a função `_resolve_selected_participation` definida em `src/penninicup/views.py` (atual ~linhas 329-341). Confirmar que nenhuma outra referência sobrou:

Run: `grep -rn "_resolve_selected_participation" src/`
Expected: nenhum resultado.

- [ ] **Step 4: Rodar testes do app penninicup**

Run: `make test-single path=src/penninicup/tests.py`
Expected: testes de Home passam. (Os de Regras serão tratados na Task 8.)

- [ ] **Step 5: Commit**

```bash
git add src/penninicup/views.py
git commit -m "refactor(home): default = primeiro bolão entrado via resolvedor compartilhado"
```

______________________________________________________________________

## Task 8: Regras — escopo às participações do usuário

**Files:**

- Modify: `src/penninicup/views.py`

- Modify: `src/penninicup/templates/penninicup/rules.html`

- Modify: `src/penninicup/tests.py`

- [ ] **Step 1: Atualizar `RulesPageTest.setUp` (owner vira participante)**

Em `src/penninicup/tests.py`, no `setUp` de `RulesPageTest`, adicionar — após criar `pool_a` e `pool_b` e antes de `config_a = ...` — as participações do owner (ordem importa: `pool_a` primeiro = default):

```python
        PoolParticipant.objects.create(pool=self.pool_a, user=self.owner, is_active=True)
        PoolParticipant.objects.create(pool=self.pool_b, user=self.owner, is_active=True)
```

(`PoolParticipant` já está importado no arquivo.) Em `test_rules_page_shows_prize_amounts_and_total_collected`, o owner já será participante de `pool_a`, então o POST seleciona `pool_a` corretamente — sem outras mudanças nesse teste.

- [ ] **Step 2: Rodar os testes de Regras — devem falhar**

Run: `make test-single path=src/penninicup/tests.py`
Expected: FAIL em `RulesPageTest` (a view ainda lista todos os pools; mas com a mudança da view virá a falha real — confirme após Step 3). Se passar aqui, ok: o ajuste de view no Step 3 mantém verde.

- [ ] **Step 3: Reescrever a view `rules`**

Em `src/penninicup/views.py`, substituir o início da função `rules` (a montagem de `pools` / `selected_pool`) por uma fonte baseada em participações. Forma final da função:

```python
@login_required
def rules(request):
    participations = list(
        PoolParticipant.objects.filter(user=request.user, is_active=True)
        .select_related("pool", "pool__season")
        .order_by("joined_at")
    )
    source = request.POST if request.method == "POST" else request.GET
    selected_slug = (source.get("pool") or "").strip()

    selected_pool = None
    if selected_slug:
        match = next((p for p in participations if p.pool.slug == selected_slug), None)
        selected_pool = match.pool if match else None
    if selected_pool is None and participations:
        selected_pool = participations[0].pool

    if request.method == "POST":
        params = {}
        if selected_pool:
            selected_pool.refresh_prize_distribution(save=True)
            messages.success(request, "Premiação atualizada com sucesso.")
            params["pool"] = selected_pool.slug
        elif selected_slug:
            params["pool"] = selected_slug
        if params:
            return redirect(f"{reverse('penninicup:rules')}?{urlencode(params)}")
        return redirect(reverse("penninicup:rules"))

    scoring_config = selected_pool.get_scoring_config() if selected_pool else None
    if selected_pool:
        selected_pool.refresh_prize_distribution(save=True)
        selected_pool.refresh_from_db()
    group_lock_at = selected_pool.get_phase_lock_time(PHASE_GROUP) if selected_pool else None
    knockout_lock_at = selected_pool.get_phase_lock_time(PHASE_KNOCKOUT) if selected_pool else None

    context = {
        "participations": participations,
        "selected_pool": selected_pool,
        "scoring_config": scoring_config,
        "group_lock_at": group_lock_at,
        "knockout_lock_at": knockout_lock_at,
        "pool_type_1": POOL_TYPE_1,
        "pool_type_2": POOL_TYPE_2,
        "group_max_points": (scoring_config.group_exact_score if scoring_config else 0),
        "knockout_max_points": (scoring_config.knockout_exact_and_advancing if scoring_config else 0),
        "bonus_total_points": (
            scoring_config.bonus_champion_points
            + scoring_config.bonus_runner_up_points
            + scoring_config.bonus_third_place_points
            + scoring_config.bonus_top_scorer_points
            if scoring_config
            else 0
        ),
        "qualifier_bonus_max": (
            scoring_config.group_qualifier_points + scoring_config.group_qualifier_position_bonus
            if scoring_config
            else 0
        ),
    }
    return render(request, "penninicup/rules.html", context)
```

A chave de contexto `pools` foi removida (substituída por `participations`).

- [ ] **Step 4: Atualizar o `<select>` em `rules.html` para iterar participações**

Em `src/penninicup/templates/penninicup/rules.html`, substituir o loop do select (atual linhas 42-47):

```html
                {% for pool in pools %}
                <option value="{{ pool.slug }}" {% if selected_pool and selected_pool.id == pool.id %}selected{% endif %}>{{ pool.name }}</option>
                {% empty %}
                <option value="">Sem bolão ativo</option>
                {% endfor %}
```

por:

```html
                {% for participation in participations %}
                <option value="{{ participation.pool.slug }}" {% if selected_pool and selected_pool.id == participation.pool.id %}selected{% endif %}>{{ participation.pool.name }}</option>
                {% empty %}
                <option value="">Sem bolão</option>
                {% endfor %}
```

- [ ] **Step 5: Rodar os testes de Regras — devem passar**

Run: `make test-single path=src/penninicup/tests.py`
Expected: PASS. `test_rules_page_loads_and_uses_default_pool` (default = pool_a, primeiro entrado) e `test_rules_page_respects_selected_pool` (owner participa de pool_b) verdes.

- [ ] **Step 6: Commit**

```bash
git add src/penninicup/views.py src/penninicup/templates/penninicup/rules.html src/penninicup/tests.py
git commit -m "feat(rules): escopa Regras aos bolões do usuário com default primeiro entrado"
```

______________________________________________________________________

## Task 9: Verificação final

- [ ] **Step 1: Suíte completa**

Run: `make test`
Expected: PASS (toda a suíte). Se algum teste fora dos arquivos tocados falhar referenciando `pool:open`, o card "Abrir bolão", ou `pools` no contexto de Regras, atualizar conforme o caso.

- [ ] **Step 2: Lint**

Run: `make lint`
Expected: ruff/format/mdformat/prettier passam. Corrigir o que o ruff apontar (imports não usados em `penninicup/views.py` após remover o helper; `pool/views.py` imports).

- [ ] **Step 3: Smoke manual (opcional, recomendado)**

Subir `make runserver`, logar, e conferir: navbar mostra Home · Partidas · Regras · Bolões · Palpites · Ranking; Palpites/Ranking abrem no primeiro bolão entrado; o seletor troca de bolão recarregando; Bolões só tem token + lista read-only.

- [ ] **Step 4: Commit final (se lint alterou arquivos)**

```bash
git add -A
git commit -m "chore: ajustes de lint pós-refactor de navegação"
```

______________________________________________________________________

## Self-Review (executado pelo autor do plano)

**Spec coverage:**

- Decisão 1 (Bolões = token + lista read-only) → Task 6 ✓
- Decisão 2 (default unificado primeiro entrado) → Tasks 1,3 (abas), 7 (home), 8 (regras) ✓
- Decisão 3 (`?pool=` query, views por baixo) → Tasks 2,3 ✓
- Decisão 4 (Regras só participações) → Task 8 ✓
- Navbar desktop+mobile → Task 5 ✓
- Partial seletor compartilhado → Task 4 ✓
- Estado vazio → Task 3 (template) ✓
- Riscos do spec (ordem de URL; botão Salvar; teste de Regras) → Task 3 Step 5, Task 5 Step 2, Task 8 ✓

**Placeholder scan:** sem TBD/TODO; todo código presente.

**Type/nome consistency:** `resolve_selected_participation` (Task 1) usado em Tasks 3,7,8. `build_ranking_dashboard_context` (Task 2) usado em Task 3. `_build_bets_context` / `_active_participations` definidos e usados em Task 3. Chaves de contexto `participations`/`selected_pool` consistentes entre views e partial. `page_kind` consistente entre `bets_tab`/`ranking_tab` e `no_pool_selected.html`.
