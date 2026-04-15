.PHONY: install
install:
	poetry install

.PHONY: install-pre-commit
install-pre-commit:
	poetry run pre-commit uninstall; poetry run pre-commit install

.PHONY: lint
lint:
	poetry run pre-commit run --all-files

.PHONY: runserver
runserver:
	poetry run python -m src.manage runserver

.PHONY: migrate
migrate:
	poetry run python -m src.manage migrate

.PHONY: makemigrations
makemigrations:
	poetry run python -m src.manage makemigrations

.PHONY: createsuperuser
createsuperuser:
	poetry run python -m src.manage createsuperuser

.PHONY: tailwind
tailwind:
	poetry run python -m src.manage tailwind start

.PHONY: test
test:
	DJANGO_SETTINGS_PROFILE=test poetry run python -m src.manage test --settings=src.config.settings --verbosity=2

.PHONY: test
up-dependencies:
	test -f .env || touch .env
	docker compose -f docker-compose.dev.yml up --force-recreate db


.PHONY: update
update: install migrate install-pre-commit ;
