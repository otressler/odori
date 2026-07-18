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
      - traefik.http.routers.cucina.rule=Host(`cucina.tail-net-name.ts.net`)
      - traefik.http.routers.cucina.entrypoints=websecure
      - traefik.http.routers.cucina.tls=true
      - traefik.http.services.cucina.loadbalancer.server.port=3000
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
    networks: [internal]

  postgres:
    image: postgres:16-alpine
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

volumes:
  postgres:
  uploads:
```

The router name deliberately remains `cucina` to match the supplied Traefik requirement; it can be renamed to `odori` only after updating dependent routing configuration.

## Required configuration

| Variable | Purpose |
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

Do not store `.env`, provider keys, database dumps, or uploaded source recipes in Git or public container registries.

## Operations

- Apply schema migrations as a release step before rolling web and worker containers to a version that requires them.
- Back up the PostgreSQL volume and uploads volume together daily; encrypt backups and test a restore at least quarterly.
- Monitor container health, database free space, failed/retried job counts, upload volume consumption, and Azure API errors/latency.
- Retain provider request metadata and job errors for troubleshooting, but redact credentials, cookies, full authorization headers, and unnecessary source content from logs.
- Prune source uploads according to a household retention setting only after confirming the extracted recipe has been approved.

## Recovery and updates

1. Put the current immutable image tag and database backup aside.
2. Pull the release image, run migrations, and deploy web/worker through Portainer or Compose.
3. Verify authenticated access through the Tailscale hostname, a recipe read, and a database health check.
4. Roll back the image only if its migrations are backward-compatible; otherwise restore the paired database and uploads backup.

## Network policy

- Traefik is the only service connected to `web`; it terminates HTTPS for the tailnet hostname.
- `postgres` and `odori-worker` have no Traefik labels and no published ports.
- Outbound application access is limited to Azure provider endpoints and explicitly permitted URL-import destinations.
- Tailscale ACLs should allow only the household's devices/users to reach the Pi service.
