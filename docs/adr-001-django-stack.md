# ADR 001: Django modular monolith

## Decision

Use Django 5.1 with server-rendered templates, Django's maintained authentication, CSRF middleware,
PostgreSQL via `psycopg`, Django migrations, pytest/pytest-django, Ruff, and a small CSS stylesheet.
The WSGI application runs under Gunicorn; a separate Django management command starts the worker.

## Rationale

Django provides mature secure sessions, CSRF handling, migrations, server-rendered pages, and an ORM
without adding services to the Pi. The application remains one image with independently invoked web
and worker processes. It is compatible with Python's Linux ARM64 images.

## Rejected alternatives

- **SPA plus a separate API:** adds build, authentication, and deployment complexity without improving
  the household-scale workflow.
- **Microservices and a queue broker:** violates the Pi resource budget and duplicates operational work.
- **SQLite in production:** does not meet the specified PostgreSQL recovery and concurrent-write model.
