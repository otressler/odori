# Deployment and operations

## Target topology

Odori runs on a Raspberry Pi 5 under Docker Compose and is managed in Portainer. Traefik provides HTTPS routing on a shared Docker `web` network. Tailscale limits access to the tailnet; the application and database must not publish host ports.

Use ARM64-compatible images and pin image versions/digests for releases. Build a multi-architecture image if development or CI runs on x86_64.

## Compose shape

```yaml
services:
  odori-web:
    image: ghcr.io/otressler/odori:${ODORI_VERSION}
    env_file: .env
    depends_on:
      postgres:
        condition: service_healthy
    volumes:
      - uploads:/app/data/uploads
    labels:
      - traefik.enable=true
      - traefik.http.routers.odori.rule=Host(`odori.tail-net-name.ts.net`)
      - traefik.http.routers.odori.entrypoints=websecure
      - traefik.http.routers.odori.tls=true
      - traefik.http.services.odori.loadbalancer.server.port=8000
    networks: [web, internal]

  odori-worker:
    image: ghcr.io/otressler/odori:${ODORI_VERSION}
    command: ./bin/worker
    env_file: .env
    depends_on:
      postgres:
        condition: service_healthy
    volumes:
      - uploads:/app/data/uploads
    networks: [internal, egress]

  postgres:
    image: postgres:16-alpine
    env_file: .env
    environment:
      POSTGRES_DB: odori
      POSTGRES_USER: odori
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U odori -d odori"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks: [internal]

networks:
  web:
    external: true
  internal:
    internal: true
  egress: {}

volumes:
  postgres:
  uploads:
```

The router name deliberately remains `cucina` to match the supplied Traefik requirement; it can be renamed to `odori` only after updating dependent routing configuration. The worker needs `egress` for recipe URLs and Azure while retaining `internal` access to PostgreSQL. A Docker egress network does not itself restrict destinations; enforce an outbound allow/deny policy with the host firewall or an explicit proxy if destination-level filtering is required.

The example uses `.env` for readability. Compose variable substitution for `${POSTGRES_PASSWORD}` comes from the shell or project-level `.env`, not another service's `env_file`. In production, prefer Docker secrets or an equivalent mounted secret and adapt the image entrypoint to read the secret file.

## Required configuration

| Variable | Purpose |
| `INGREDIENT_EMBEDDINGS_ENABLED` | Enables Azure OpenAI semantic ingredient matching; disable to use local fuzzy matching only. |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | Azure OpenAI embedding deployment name used for ingredient vectors. |
| `AZURE_OPENAI_IMAGE_DEPLOYMENT` | Microsoft Foundry `gpt-image-2` deployment name used by the worker for recipe-card images. Defaults to `gpt-image-2` and may be overridden for a differently named deployment. |
| `AZURE_OPENAI_IMAGE_API_VERSION` | Image generation API version; defaults to `2025-04-01-preview`. |
| `AZURE_OPENAI_IMAGE_TIMEOUT_SECONDS` | Maximum image-generation request duration; defaults to `60`. |
| `WORKER_HEARTBEAT_MAX_AGE_SECONDS` | Maximum age of a worker heartbeat before `/health/worker` and the owner operations page report it as unavailable. Defaults to `30`. |
| --- | --- |
| `ODORI_VERSION` | Immutable application release tag. |
| `DATABASE_URL` | PostgreSQL connection string on the internal Docker network. |
| `POSTGRES_PASSWORD` | Database password, supplied via Docker secret where practical. |
| `SESSION_SECRET` | High-entropy secret used to sign/encrypt sessions. |
| `AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT` | Azure Document Intelligence endpoint. |
| `AZURE_DOCUMENT_INTELLIGENCE_KEY` | Provider credential. |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint. |
| `AZURE_OPENAI_API_KEY` | Provider credential. |
| `AZURE_OPENAI_DEPLOYMENT` | Approved model deployment name. |
| `UPLOAD_MAX_BYTES` | Enforced document upload limit. |
| `ALLOWED_TAILNET_HOST` | Expected host for origin checks and URLs. |
| `IMPORT_WORKER_CONCURRENCY` | Concurrent import jobs; default `1` on the Pi. |
| `AI_IMPORT_ENABLED` | Emergency/operational switch for billable document and normalization calls. |
| `AI_GENERATION_ENABLED` | Independent switch for generated recipe calls. |
| `AI_DAILY_JOB_LIMIT` | Per-household daily billable-job guardrail. |
| `AI_MAX_INPUT_CHARS` | Maximum text sent to a language model per job. |
| `AI_MAX_OUTPUT_TOKENS` | Maximum model output tokens per request. |

Do not store `.env`, provider keys, database dumps, or uploaded source recipes in Git or public container registries.

## Resource and cost controls

- Set container memory limits appropriate to an 8 GB Pi and leave at least 2 GB for the host, Traefik, Tailscale, Portainer, and filesystem cache. Start with one web process, one worker, worker concurrency `1`, and a bounded PostgreSQL connection pool.
- Configure Azure Cost Management monthly budget alerts at USD 20 and USD 35. Alerts do not stop spending, so keep document import and generated recipes behind independent switches that an operator can disable before the USD 50 ceiling.
- Target less than USD 15/month in normal Azure usage. Set per-household page, job, input-token, output-token, and retry limits; review regional provider prices before enabling production deployments.
- Do not provision always-on Azure compute. If Flex Consumption is later justified by measurements, configure zero always-ready instances and retain local feature fallbacks.
- Cache provider work by source content hash and processing version. Report cache hits, pages/characters/tokens submitted, retries, and estimated cost by provider without logging recipe content.
- Recipe creation and manual regeneration enqueue a durable image job. The `odori-worker` service is the only component that calls the Microsoft Foundry image deployment; a queued card continues to show its placeholder until the worker persists a generated image.
- Keep ordinary recipe, pantry, plan, shopping, and cooking traffic local. An exhausted cloud budget must disable only assisted features.

## Operations

- Apply schema migrations as a release step before rolling web and worker containers to a version that requires them.
- Back up the PostgreSQL volume and uploads volume together daily; encrypt backups and test a restore at least quarterly.
- Provide an authenticated recipe export (structured JSON plus optionally printable recipes) so a household can retain its approved catalog independently of infrastructure backups.
- Monitor container health, database free space, failed/retried job counts, upload volume consumption, and Azure API errors/latency.
- Monitor active WebSocket connections, reconnect rates, event delivery failures, and persistent client version gaps; a reconnect must always recover through REST state reads.
- Retain provider request metadata and job errors for troubleshooting, but redact credentials, cookies, full authorization headers, and unnecessary source content from logs.
- The application retains at most 200 sanitized provider diagnostic records per household. These records contain outcome codes, deployment names, HTTP status, vector dimensions, durations, and correlation IDs; they never contain prompts, embeddings, provider payloads, cookies, or credentials.
- Prune source uploads according to a household retention setting only after confirming the extracted recipe has been approved.

## Recovery and updates

1. Put the current immutable image tag and database backup aside.
2. Pull the release image, run migrations, and deploy web/worker through Portainer or Compose.
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

The category test at `/admin/categories` reports its embedding outcome, model deployment,
dimensions, text similarity, cosine similarity, and final score. The final score is the greater of
the text and cosine scores; no hidden weights are applied.

To validate embedding connectivity from inside the web container, run:

```bash
python scripts/check_embedding_connectivity.py
```

## Network policy

- Traefik is the only service connected to `web`; it terminates HTTPS for the tailnet hostname.
- Configure Traefik to support standard HTTPS WebSocket upgrades on the same `cucina` router; no additional public port or separate socket hostname is needed.
- `postgres` and `odori-worker` have no Traefik labels and no published ports.
- Outbound application access is limited to Azure provider endpoints and explicitly permitted URL-import destinations.
- Tailscale ACLs should allow only the household's devices/users to reach the Pi service.
- Bind the Traefik entrypoint or host firewall rule to the Tailscale interface/address. A tailnet hostname and ACL do not make a router private if the same entrypoint is also reachable from a public interface.
