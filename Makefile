.PHONY: format lint test migrate seed build

format:
	python -m ruff format .
lint:
	python -m ruff check .
test:
	python -m pytest
migrate:
	python manage.py migrate
seed:
	python manage.py seed_demo
build:
	docker build --platform linux/arm64 -t odori:local .
