.PHONY: format lint test test-container migrate seed build

format:
	python -m ruff format .
lint:
	python -m ruff check .
test:
	python -m pytest
test-container:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm --build test
migrate:
	python manage.py migrate
seed:
	python manage.py seed_demo
build:
	docker build --platform linux/arm64 -t odori:local .
