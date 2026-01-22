.PHONY: install
install:
	poetry install

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
superuser:
	poetry run python -m src.manage createsuperuser

.PHONY: update
update: install migrate ;