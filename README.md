# Odori

Odori is a private, AI-assisted meal-planning web app for a household. It turns recipe links and documents into a reusable catalog, tracks pantry availability at a deliberately coarse level, recommends meals that use what is available, and derives shopping lists from a weekly plan.

The product is designed for a Raspberry Pi 5 deployment behind Traefik and Tailscale. See [the documentation](docs/README.md) for the implementation-ready specification.

## Development

Install Python 3.10+, then run `python -m pip install -r requirements.txt`, `python manage.py migrate`,
and `python manage.py bootstrap_owner --username mara --household "Unser Haushalt"`. Start the app with
`python manage.py runserver`. On systems with GNU Make, `make lint`, `make test`, and `make build` run
the standard checks. In Windows PowerShell, use `python -m ruff check .`, `python -m pytest`, and
`docker build --platform linux/arm64 -t odori:local .` instead.

To enable Google authentication, follow the [Google sign-in integration guide](docs/google-sign-in.md).

Production uses `docker-compose.yml` behind Traefik. Copy `.env.example` to `.env`, provide unique
secrets, start the stack (the one-shot `odori-migrate` service applies migrations before web and
worker start), bootstrap the initial owner exactly once, and use `python scripts/smoke.py` with
`ODORI_SMOKE_URL` after deployment.

Household owners can inspect worker, queue, provider, and embedding diagnostics at
`/admin/operations`. See [deployment operations](docs/deployment-operations.md#observability-and-troubleshooting)
for correlation IDs, health endpoints, and Docker log commands.

## Container releases

GitHub Actions publishes multi-architecture (`linux/amd64` and `linux/arm64`) images to
`ghcr.io/otressler/odori` on every push to `main` and release tag matching `v*`. For a Pi deployment,
create and push a release tag, then set `ODORI_VERSION` in the Portainer stack to that tag without the
leading `v` only if you tag that way; otherwise use the tag exactly as published:

```bash
git tag v1.0.0
git push origin v1.0.0
```

```dotenv
ODORI_VERSION=v1.0.0
```

The workflow also publishes `sha-<commit>` tags. Prefer a release or SHA tag over the mutable `main`
and `latest` tags. Make the `odori` GHCR package public, or configure a GitHub Container Registry
credential in Portainer that has `read:packages` permission before deploying a private package.
