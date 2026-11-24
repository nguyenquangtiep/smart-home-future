PYTHON ?= python

.PHONY: install lint format test eval check run docker-build up down

install:
	$(PYTHON) -m pip install -e ".[dev]"

lint:
	ruff check .
	ruff format --check .

format:
	ruff check --fix .
	ruff format .

test:
	$(PYTHON) -m pytest

eval:
	$(PYTHON) eval/scripts/run_eval.py

check: lint test eval

run:
	uvicorn smarthome.api.main:app --reload --host 127.0.0.1 --port 8000

docker-build:
	docker compose build

up:
	docker compose up -d --build

down:
	docker compose down
