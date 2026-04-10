# PenniniCup

Plataforma de bolao da Copa com palpites por fase, ranking em tempo real, regras dinamicas por bolao e fluxo de pagamentos.

## Visao Geral

O projeto e uma aplicacao Django monolitica com apps separados por dominio:

- `football`: sincronizacao de times, jogos, standings e chaveamento.
- `pool`: boloes, palpites, regras de bloqueio e calculo de pontuacao.
- `rankings`: classificacao e criterios de desempate.
- `payments`: pagamentos e validacao de acesso a palpites.
- `accounts` e `penninicup`: autenticacao, perfil, dashboard e paginas institucionais.

## Stack Tecnica

- Python 3.12
- Django 6
- Poetry para gerenciamento de dependencias
- TailwindCSS (via app `theme`)
- Banco local padrao: SQLite (dev)

## Timezone Oficial

Todo o projeto considera **Horario de Brasilia**.

- `TIME_ZONE = "America/Sao_Paulo"`
- `USE_TZ = True`
- Prazos de fechamento e datas de jogo sao tratados como datetimes aware.

## Requisitos

Antes de iniciar, tenha instalado:

- Python 3.12+
- Poetry
- Node.js 20+ e npm (para assets do tema)
- Git

## Como Clonar e Rodar Localmente

1. Clonar o repositorio

```bash
git clone <URL_DO_REPOSITORIO>
cd PenniniCup
```

2. Instalar dependencias Python

```bash
poetry install
```

3. Criar configuracao local

```bash
mkdir -p local
cp src/config/settings/templates/settings.dev.py local/settings.dev.py
```

4. (Opcional, recomendado) criar `.env`

Crie um arquivo `.env` na raiz do projeto com as variaveis necessarias ao seu ambiente.

5. Aplicar migrations

```bash
poetry run python -m src.manage migrate
```

6. Criar superusuario

```bash
poetry run python -m src.manage createsuperuser
```

7. Instalar dependencias front do tema

```bash
cd src/theme/static_src
npm install
cd ../../..
```

8. Rodar o servidor

```bash
poetry run python -m src.manage runserver
```

Aplicacao disponivel em:

- `http://127.0.0.1:8000`

## Comandos Uteis (Makefile)

```bash
make install              # poetry install
make migrate              # aplica migrations
make makemigrations       # cria migrations
make runserver            # sobe o Django
make tailwind             # watch/build do css do tema
make test                 # roda testes com perfil de teste
make lint                 # pre-commit em todos os arquivos
make update               # install + migrate + install-pre-commit
```

## Fluxo de Dados da Copa (Sync)

Comandos de sincronizacao disponiveis em `football`:

- `sync_groups`
- `sync_teams`
- `sync_players`
- `sync_matches`
- `sync_standings`
- `sync_knockout`
- `import_assignthird`

Exemplo:

```bash
poetry run python -m src.manage sync_matches
```

## Testes

Rodar a suite completa:

```bash
DJANGO_SETTINGS_PROFILE=test poetry run python -m src.manage test --settings=src.config.settings --verbosity=2
```

Rodar um modulo especifico:

```bash
DJANGO_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests
```

## Estrutura de Pastas (resumo)

```text
src/
  accounts/
  config/
  football/
  payments/
  penninicup/
  pool/
  rankings/
  templates/
  theme/
```

## Limpeza e Arquivos Locais

Arquivos que **nao devem** ser versionados:

- banco local (`db.sqlite3`)
- logs (`logs/`)
- uploads locais (`media/`)
- ambientes virtuais (`.venv/`)
- cache (`.ruff_cache/`, `__pycache__/`)

Se precisar limpar ambiente local rapidamente (Linux/macOS):

```bash
rm -rf logs media .ruff_cache
find . -type d -name "__pycache__" -prune -exec rm -rf {} +
```

No Windows (PowerShell):

```powershell
Remove-Item -Recurse -Force logs, media, .ruff_cache -ErrorAction SilentlyContinue
Get-ChildItem -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
```

## Licenca

MIT. Veja o arquivo `LICENSE`.
