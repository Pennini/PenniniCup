# 🏗️ Plano de Arquitetura — App `football` (API + Models + Views)

> Tudo relacionado a jogos, times e classificação vive no app `football`.
> O app `matches` será **removido** após migração.

______________________________________________________________________

## 📁 Estrutura Final

```
src/football/
│
├── api/                         # 🌐 Comunicação com a API externa
│   ├── __init__.py
│   ├── client.py                # Client HTTP (autenticação, retry, rate limit)
│   ├── endpoints.py             # Constantes de URLs e códigos da API
│   └── exceptions.py            # Exceções customizadas (APIError, RateLimitError)
│
├── services/                    # 🔄 Lógica de sincronização (API → banco)
│   ├── __init__.py
│   ├── sync_teams.py            # Busca times da API → salva em Team
│   ├── sync_matches.py          # Busca partidas → salva em Match
│   ├── sync_standings.py        # Busca classificação → salva em GroupEntry
│   └── sync_knockout.py         # Resolve chaves do mata-mata
│
├── management/commands/         # ⚙️ Comandos CLI (entry points)
│   ├── __init__.py
│   ├── sync_teams.py            # python manage.py sync_teams
│   ├── sync_matches.py          # python manage.py sync_matches
│   └── sync_standings.py        # python manage.py sync_standings
│
├── models.py                    # 📦 Team, Match, GroupStage, GroupEntry, Knockout...
├── admin.py                     # Django Admin para todos os models
├── views.py                     # Views: lista de jogos, bracket, standings
├── urls.py                      # Rotas do app
├── forms.py                     # (futuro) Formulários se necessário
├── tests/                       # Testes organizados por camada
│   ├── __init__.py
│   ├── test_client.py
│   ├── test_sync_teams.py
│   └── test_sync_matches.py
│
└── templates/football/          # Templates do app
    ├── match_list.html
    └── ...
```

______________________________________________________________________

## 🧠 O Papel de Cada Camada

### `api/` — O Fornecedor de Dados

**Função:** Fazer chamadas HTTP para a football-data.org e devolver o JSON cru.
**NÃO faz:** Salvar no banco, transformar dados, nem conhecer os models.

```python
# api/client.py — Exemplo de uso
client = FootballDataClient()
json_times = client.get_teams("WC")  # retorna lista de dicts
json_jogos = client.get_matches("WC")  # retorna lista de dicts
```

**Por que separar?** Se amanhã a API mudar (nova versão, nova URL, novo formato de autenticação), você mexe **só aqui**. O resto do app não é afetado.

______________________________________________________________________

### `services/` — O Cérebro

**Função:** Pegar o JSON da `api/`, transformar nos campos corretos e salvar nos `models`.
**É chamado por:** management commands (e no futuro, por Celery tasks).

```python
# services/sync_teams.py — Exemplo de uso
from football.api.client import FootballDataClient
from football.models import Team


def sync_teams():
    client = FootballDataClient()
    teams_json = client.get_teams("WC")

    for t in teams_json:
        Team.objects.update_or_create(
            api_id=t["id"],
            defaults={
                "name": t["name"],
                "short_name": t["shortName"],
                "code": t["tla"],
                "crest_url": t["crest"],
                "api_fonte": "football-data.org",
            },
        )
```

**Por que separar da view?** Porque a view é pra **mostrar** dados, não pra **buscar da API**. E o management command é pra **disparar** a sync, não pra conter a lógica toda.

______________________________________________________________________

### `management/commands/` — Os Botões

**Função:** Comando que você roda no terminal para disparar uma sincronização.

```bash
python src/manage.py sync_teams          # Importa times
python src/manage.py sync_matches        # Importa/atualiza partidas
python src/manage.py sync_standings      # Importa classificação dos grupos
```

**Por dentro, é simples:**

```python
# management/commands/sync_teams.py
from django.core.management.base import BaseCommand
from football.services.sync_teams import sync_teams


class Command(BaseCommand):
    help = "Sincroniza times da Copa via API football-data.org"

    def handle(self, *args, **options):
        result = sync_teams()
        self.stdout.write(f"Times criados: {result.created}, atualizados: {result.updated}")
```

______________________________________________________________________

### `models.py` — O Banco de Dados

Migrar os models atuais do `matches` para cá (**mesmos models, mesmo código**):

- `Team`, `Player`, `Match`
- `GroupStage`, `GroupEntry`
- `Knockout`, `KnockoutSlot`

______________________________________________________________________

### `views.py` + `templates/` — A Interface

Migrar views e templates do `matches` para cá. A view `match_list` já refatorada (sem N+1) vai para `football/views.py`.

______________________________________________________________________

## 🔗 Como Tudo Se Conecta (Fluxo Completo)

```
1. Você roda:     python manage.py sync_teams
                        │
2. Chama:         management/commands/sync_teams.py
                        │
3. Que chama:     services/sync_teams.py
                        │
4. Que usa:       api/client.py  ──HTTP GET──▶  football-data.org
                        │                              │
5. Retorna JSON:  ◀────────────────────────────────────┘
                        │
6. Service salva: Team.objects.update_or_create(...)  →  banco de dados
                        │
7. Usuário acessa: /football/  →  views.py lê Team/Match do banco  →  template renderiza
```

______________________________________________________________________

## 📋 Passos de Migração (matches → football)

1. Copiar `matches/models.py` → `football/models.py`
1. Copiar `matches/admin.py` → `football/admin.py`
1. Copiar `matches/views.py` → `football/views.py`
1. Mover `matches/templates/` → `football/templates/football/`
1. Atualizar `config/urls.py`: trocar `src.matches.urls` → `src.football.urls`
1. Atualizar `INSTALLED_APPS`: remover `src.matches`
1. Criar migration para mover as tabelas (ou resetar o banco em dev)
1. Atualizar imports em outros apps que referenciem `matches.Team` → `football.Team`
1. Remover o app `matches/`

______________________________________________________________________

## ⚙️ Mudanças em Settings e Dependências

### `config/settings/base.py`

```python
# Adicionar:
FOOTBALL_DATA_API_KEY = os.getenv("API_FOOTBALL", "")
FOOTBALL_DATA_COMPETITION = "WC"

# Remover de INSTALLED_APPS:
# "src.matches.apps.MatchesConfig",
```

### `pyproject.toml`

```toml
# Adicionar à seção [tool.poetry.dependencies]:
httpx = "^0.28"
```

______________________________________________________________________

## ⚠️ Pontos de Atenção

1. **`.env` no repositório** — Expõe chave da API, credenciais Gmail e tokens Mercado Pago. Adicionar ao `.gitignore`.
1. **Rate limit** — API free = 10 req/min. O `client.py` deve implementar retry com backoff.
1. **Copa 2026 (48 times)** — Formato expandido com 12 grupos + 32 avos. O `R32` já está no `STAGE_CHOICES`.

______________________________________________________________________

## 🗺️ Mapeamento API → Models

### Times

| Campo API   | Campo Model (`Team`) |
| ----------- | -------------------- |
| `id`        | `api_id`             |
| `name`      | `name`               |
| `shortName` | `short_name`         |
| `tla`       | `code`               |
| `crest`     | `crest_url`          |

### Partidas

| Campo API              | Campo Model (`Match`)         |
| ---------------------- | ----------------------------- |
| `id`                   | `api_id`                      |
| `homeTeam.id`          | `home_team` (FK via `api_id`) |
| `awayTeam.id`          | `away_team` (FK via `api_id`) |
| `score.fullTime.home`  | `home_score`                  |
| `score.fullTime.away`  | `away_score`                  |
| `utcDate`              | `start_time`                  |
| `stage`                | `stage` (mapeado p/ choices)  |
| `matchday`             | `api_matchday`                |
| `status == "FINISHED"` | `finished = True`             |
| `lastUpdated`          | `api_ultimo_update`           |

### Mapeamento de Stages

| API (`stage`)    | Model (`STAGE_CHOICES`) |
| ---------------- | ----------------------- |
| `GROUP_STAGE`    | `GROUP`                 |
| `LAST_32`        | `R32`                   |
| `LAST_16`        | `R16`                   |
| `QUARTER_FINALS` | `QF`                    |
| `SEMI_FINALS`    | `SF`                    |
| `FINAL`          | `FINAL`                 |
