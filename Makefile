.PHONY: install
install:
	poetry install

.PHONY: runserver
runserver:
	poetry run python -m penninibet.manage runserver

.PHONY: migrate
migrate:
	poetry run python -m penninibet.manage migrate

.PHONY: makemigrations
makemigrations:
	poetry run python -m penninibet.manage makemigrations

.PHONY: createsuperuser
superuser:
	poetry run python -m penninibet.manage createsuperuser

.PHONY: update
update: install migrate ;