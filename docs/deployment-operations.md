# Deployment and operations

## Target topology

Odori runs on a Raspberry Pi 5 under Docker Compose and is managed in Portainer. Traefik provides HTTPS routing on a shared external Docker `proxy` network. Tailscale limits access to the tailnet; the application and database must not publish host ports.

Use ARM64-compatible images and pin image versions/digests for releases. Build a multi-architecture image if development or CI runs on x86_64.

## Compose shape

This is a topology overview; `docker-compose.yml` is authoritative for the complete environment
mapping and the worker healthcheck command.

```yaml
services:
  odori-web:
    image: ghcr.io/otressler/odori:${ODORI_VERSION}
    command: gunicorn --bind 0.0.0.0:8000 --workers 1 --access-logfile - --error-logfile - --capture-output odori.wsgi:application
    depends_on:
      postgres:
        condition: service_healthy
      odori-migrate:
        condition: service_completed_successfully
    volumes:
      - uploads:/app/data/uploads
    labels:
      - traefik.enable=true
      - traefik.http.routers.odori.rule=Host(`odori.tail-net-name.ts.net`)
      - traefik.http.routers.odori.entrypoints=websecure
      - traefik.http.routers.odori.tls=true
      - traefik.http.services.odori.loadbalancer.server.port=8000
    networks: [proxy, internal]

  odori-worker:
    image: ghcr.io/otressler/odori:${ODORI_VERSION}
    command: python manage.py worker
    depends_on:
      postgres:
        condition: service_healthy
      odori-migrate:
        condition: service_completed_successfully
    volumes:
      - uploads:/app/data/uploads
    networks: [internal, egress]
    healthcheck:
      test: ["CMD", "python", "manage.py", "shell", "-c", "<checks the default WorkerHeartbeat is fresh>"]
      interval: 15s
      timeout: 5s
      retries: 3
      start_period: 20s

  odori-migrate:
    image: ghcr.io/otressler/odori:${ODORI_VERSION}
    command: python manage.py migrate --noinput
    depends_on:
      postgres:
        condition: service_healthy
    networks: [internal]
    restart: "no"

  postgres:
    image: postgres:18.4-alpine
    environment:
      POSTGRES_DB: odori
      POSTGRES_USER: odori
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres:/var/lib/postgresql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U odori -d odori"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks: [internal]

networks:
  proxy:
    external: true
  internal:
    internal: true
  egress: {}

volumes:
  postgres:
  uploads:
```

The router is named `odori`. The worker needs `egress` for recipe URLs and Azure while retaining
`internal` access to PostgreSQL. A Docker egress network does not itself restrict destinations;
enforce an outbound allow/deny policy with the host firewall or an explicit proxy if
destination-level filtering is required.

The checked-in Compose file maps application variables explicitly; a project-level `.env` supplies
values for Compose substitution but is not automatically injected into a container. Keep the
mapping and `.env.example` in sync when adding a runtime setting. In production, prefer Docker
secrets or an equivalent mounted secret and adapt the application configuration to read the secret
file.

## Required configuration

| Variable | Purpose |
| --- | --- |
| `ODORI_VERSION` | Required immutable GHCR image tag. Use a release or `sha-<commit>` tag, not `latest`. |
| `DATABASE_URL` | Required PostgreSQL connection string using the internal `postgres` hostname. |
| `POSTGRES_PASSWORD` | Required database password. |
| `SESSION_SECRET` | Required high-entropy Django signing secret. |
| `DEBUG` | Keep `false` in production; it defaults to `false` if omitted. |
| `ALLOWED_TAILNET_HOST`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` | Required public hostname and HTTPS origin configuration for Traefik/Tailscale. |
| `SECURE_SSL_REDIRECT`, `SECURE_HSTS_SECONDS`, `SECURE_HSTS_PRELOAD` | Production transport-security settings; the supplied defaults enable redirect and one-year HSTS. |
| `INGREDIENT_EMBEDDINGS_ENABLED` | Enables Azure OpenAI semantic ingredient matching; leave `false` for local fuzzy matching only. |
| `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | Azure OpenAI endpoint, credential, and embedding deployment used when embeddings are enabled. |
| `AZURE_OPENAI_IMAGE_DEPLOYMENT`, `AZURE_OPENAI_PANTRY_ICON_DEPLOYMENT` | Image deployment names used by the worker; the former defaults to `gpt-image-2`, while the latter is optional. |
| `AZURE_OPENAI_PANTRY_ICON_NATIVE_TRANSPARENCY` | Set `true` only when the pantry icon deployment supports native transparent backgrounds; otherwise leave it unset or set it to `false` to use white-background icons with local postprocessing (default `false`). |
| `AZURE_OPENAI_IMAGE_API_VERSION`, `AZURE_OPENAI_IMAGE_TIMEOUT_SECONDS`, `AZURE_OPENAI_IMAGE_MIN_INTERVAL_SECONDS` | Image API version, request timeout (default `60`), and minimum interval between image requests in the worker (default `12`). |
| `RECIPE_GENERATION_ENABLED`, `AZURE_OPENAI_RECIPE_GENERATION_DEPLOYMENT`, `AZURE_OPENAI_RECIPE_GENERATION_TIMEOUT_SECONDS`, `RECIPE_GENERATION_DAILY_LIMIT` | Enable queued recipe generation, select its chat-model deployment, and set its worker timeout (default `30`) and per-household rolling 24-hour limit (default `3`). |
| `WORKER_HEARTBEAT_MAX_AGE_SECONDS` | Heartbeat freshness threshold for `/health/worker`, the operations page, and the worker container healthcheck (default `30`). |
| `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` | Optional Google sign-in credentials for the web service; see [Google sign-in](google-sign-in.md). |

`ORBIT_DATABASE_URL`, `ORBIT_STORAGE_LIMIT`, `ORBIT_ENABLED`, and related `ORBIT_*` settings are
recognized by Django but deliberately are not mapped by the supplied Compose file. Add them only
with the dedicated telemetry database and migration procedure described below.

### Planned, not current configuration

The following names appear in older planning material but are not read by the application or mapped
by Compose: `AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT`, `AZURE_DOCUMENT_INTELLIGENCE_KEY`,
`AZURE_OPENAI_DEPLOYMENT`, `UPLOAD_MAX_BYTES`, `IMPORT_WORKER_CONCURRENCY`,
`AI_IMPORT_ENABLED`, `AI_GENERATION_ENABLED`, `AI_DAILY_JOB_LIMIT`, `AI_MAX_INPUT_CHARS`, and
`AI_MAX_OUTPUT_TOKENS`. They are planned controls, not operational switches; do not set them
expecting enforcement.

Do not store `.env`, provider keys, database dumps, or uploaded source recipes in Git or public container registries.

## Resource and cost controls

- Set container memory limits appropriate to an 8 GB Pi and leave at least 2 GB for the host,
  Traefik, Tailscale, Portainer, and filesystem cache. Start with one web process and one worker;
  the current worker processes jobs serially.
- Configure Azure Cost Management monthly budget alerts at USD 20 and USD 35. Alerts do not stop
  spending. The `AI_*` emergency switches and per-household spend limits are not implemented yet,
  so use provider credential rotation or provider-side limits for an emergency stop.
- Target less than USD 15/month in normal Azure usage. Per-household page, job, input-token,
  output-token, and retry limits remain planned work; review regional provider prices before
  enabling production deployments.
- Do not provision always-on Azure compute. If Flex Consumption is later justified by measurements, configure zero always-ready instances and retain local feature fallbacks.
- Cache provider work by source content hash and processing version. Report cache hits, pages/characters/tokens submitted, retries, and estimated cost by provider without logging recipe content.
- Recipe creation and manual regeneration enqueue a durable image job. The `odori-worker` service is the only component that calls the Microsoft Foundry image deployment; a queued card continues to show its placeholder until the worker persists a generated image.
- Keep ordinary recipe, pantry, plan, shopping, and cooking traffic local. An exhausted cloud budget must disable only assisted features.

## Operations

- `odori-migrate` is the sole migration runner. On `docker compose up -d`, it runs
  `python manage.py migrate --noinput` after PostgreSQL is healthy; web and worker wait for it to
  finish successfully. This avoids migration races even if either application service is later
  scaled. A migration failure intentionally prevents the new web and worker containers starting.
- Back up the PostgreSQL volume and uploads volume together daily; encrypt backups and test a restore at least quarterly.
- Provide an authenticated recipe export (structured JSON plus optionally printable recipes) so a household can retain its approved catalog independently of infrastructure backups.
- Monitor container health, database free space, failed/retried job counts, upload volume consumption, and Azure API errors/latency.
- Monitor active WebSocket connections, reconnect rates, event delivery failures, and persistent client version gaps; a reconnect must always recover through REST state reads.
- Retain provider request metadata and job errors for troubleshooting, but redact credentials, cookies, full authorization headers, and unnecessary source content from logs.
- The application retains at most 200 sanitized provider diagnostic records per household. These records contain outcome codes, deployment names, HTTP status, vector dimensions, durations, and correlation IDs; they never contain prompts, embeddings, provider payloads, cookies, or credentials.
- Prune source uploads according to a household retention setting only after confirming the extracted recipe has been approved.

## Recovery and updates

1. Put the current immutable image tag and database backup aside.
2. Set the release image tag, deploy through Portainer or Compose, and confirm `odori-migrate`
   completed successfully before accepting web and worker traffic.
3. Verify authenticated access through the Tailscale hostname, a recipe read, and a database health check.
4. Roll back the image only if its migrations are backward-compatible; otherwise restore the paired database and uploads backup.

## Observability and troubleshooting

Every web and worker record is emitted as JSON to container stdout/stderr. Each web response returns
an `X-Request-ID`; use it to correlate a browser failure with logs and any queued job:

```bash
docker compose logs --tail=200 odori-web odori-worker
docker compose logs --tail=500 odori-web odori-worker | grep '<request-id-or-job-id>'
```

The authenticated **Betrieb** page at `/admin/operations` is available only to household owners. It
shows database/worker freshness, queue counts and recent job attempts, sanitized provider outcomes,
and ingredient/category embedding coverage. Failed category and image jobs can be explicitly
requeued there; retries retain their attempt count and receive a new correlation ID.

For automated checks, use:

- `/health/live` for process liveness;
- `/health/ready` for database readiness; and
- `/health/worker` for worker-heartbeat readiness.

The worker's Compose healthcheck runs the same freshness test directly against its `WorkerHeartbeat`
row. It does not depend on the web container or on external proxy DNS.

The category test at `/admin/categories` reports its embedding outcome, model deployment,
dimensions, text similarity, cosine similarity, and final score. The final score is the greater of
the text and cosine scores; no hidden weights are applied.

### Django Orbit

Django Orbit is the central source for request, query, log, exception, transaction, storage, and
supported client telemetry. Its dashboard is at `/orbit/` and requires a Django superuser because
its evidence may span households. The existing **Betrieb** page remains the source for curated
household diagnostics, worker freshness, and custom polling-worker job state.

Orbit keeps at most `ORBIT_STORAGE_LIMIT` entries (default `5000`). Set
`ORBIT_DATABASE_URL` to use a dedicated telemetry database, then run:

```bash
python manage.py migrate orbit --database=orbit
```

Without `ORBIT_DATABASE_URL`, `odori-migrate` applies the Orbit migrations to the default database.
In production, leave `ORBIT_MCP_ENABLED` unset or `false`; local
development enables metadata-only MCP access by default. Never enable payload access or loosen
the configured masking without an explicit data-retention review.

To validate embedding connectivity from inside the web container, run:

```bash
python scripts/check_embedding_connectivity.py
```

## Network policy

- Traefik is the only service connected to `proxy`; it terminates HTTPS for the tailnet hostname.
- Configure Traefik to support standard HTTPS WebSocket upgrades on the `odori` router; no additional public port or separate socket hostname is needed.
- `postgres` and `odori-worker` have no Traefik labels and no published ports.
- Outbound application access is limited to Azure provider endpoints and explicitly permitted URL-import destinations.
- Tailscale ACLs should allow only the household's devices/users to reach the Pi service.
- Bind the Traefik entrypoint or host firewall rule to the Tailscale interface/address. A tailnet hostname and ACL do not make a router private if the same entrypoint is also reachable from a public interface.
