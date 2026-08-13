# Odori Agent Instructions

## Test workflow

Use the containerized test target as the canonical test command for GitHub agents:

```sh
make test-container
```

This command builds the `test` Docker target, installs all packages from `requirements.txt`, and runs `pytest` with SQLite at `sqlite:////tmp/odori-test.sqlite3`. It does not require the host Python environment or a PostgreSQL test database.

Do not run `pytest` inside `odori-web`: that is the production-style image and intentionally contains only `requirements-prod.txt`, so test-only packages are unavailable there.

The host `make test` target is appropriate only when the active virtualenv has `requirements.txt` installed and its database URL points to a reachable database. The Compose hostname `postgres` resolves only inside the Compose network; it does not resolve from macOS. For a direct container run, use:

```sh
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm --build test
```

For a focused test:

```sh
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm --build test python manage.py test core.test_pages.PageRenderTests.test_name
```

The test image also includes Ruff, so lint can be run without host package setup:

```sh
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm --build test python -m ruff check .
```

If dependencies or source files changed, retain `--build`. Do not change production dependencies merely to make tests available in the web image.
