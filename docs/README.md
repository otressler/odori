# Odori documentation

| Document | Purpose |
| --- | --- |
| [Product requirements](product-requirements.md) | Scope, user journeys, requirements, acceptance criteria, and delivery phases |
| [Architecture](architecture.md) | System context, components, integrations, workflows, and technology guidance |
| [Domain model](domain-model.md) | Data entities, relationships, lifecycle rules, and inventory semantics |
| [API specification](api-specification.md) | HTTP resources, asynchronous jobs, and error conventions |
| [Deployment and operations](deployment-operations.md) | Raspberry Pi, Docker Compose, Traefik, Tailscale, configuration, backups, and observability |

The source brief makes no framework choice. The documents therefore define stable boundaries and recommend a single deployable, server-rendered application with a relational database for the first release. This keeps the Pi deployment simple while allowing a later split into workers or services if workload requires it.
