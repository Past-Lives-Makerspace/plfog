.DEFAULT_GOAL := help

# Load .env if it exists
ifneq (,$(wildcard .env))
    include .env
    export
endif

.PHONY: help setup install db-up db-down db-pull-prod migrate server test lint format typecheck

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' Makefile | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

setup: ## First-time setup: .env, venv, deps, migrate
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example — edit it if needed")
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r requirements.txt
	@echo ""
	@echo "Setup complete. Next steps:"
	@echo "  1. make db-up        (start PostgreSQL)"
	@echo "  2. make migrate      (create tables)"
	@echo "  3. make server       (start dev server)"
	@echo ""
	@echo "Optional: set PROD_DATABASE_URL in .env, then run 'make db-pull-prod' to load production data."

install: ## Install Python dependencies into venv
	.venv/bin/pip install -r requirements.txt

db-up: ## Start PostgreSQL via Docker Compose
	docker compose up -d
	@echo "PostgreSQL running on localhost:5432"

db-down: ## Stop PostgreSQL
	docker compose down

db-pull-prod: ## Download production database into local Postgres
	.venv/bin/python manage.py pull_prod_db

migrate: ## Run Django migrations
	.venv/bin/python manage.py migrate

server: ## Start Django dev server
	.venv/bin/python manage.py runserver

test: ## Run tests
	.venv/bin/pytest

lint: ## Run ruff linter and formatter check
	.venv/bin/ruff check . && .venv/bin/ruff format --check .

format: ## Auto-format code with ruff
	.venv/bin/ruff format . && .venv/bin/ruff check --fix .

typecheck: ## Run mypy (django-stubs needs Django to boot, so supply a DATABASE_URL)
	DATABASE_URL="$${DATABASE_URL:-sqlite://:memory:}" .venv/bin/python -m mypy .
