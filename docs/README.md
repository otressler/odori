# Odori documentation

| Document | Purpose |
| --- | --- |
| [Product requirements](product-requirements.md) | Scope, user journeys, requirements, acceptance criteria, collaboration, and delivery phases |
| [Architecture](architecture.md) | System context, components, integrations, workflows, real-time collaboration, and technology guidance |
| [Domain model](domain-model.md) | Data entities, relationships, lifecycle rules, and inventory semantics |
| [API specification](api-specification.md) | HTTP resources, asynchronous jobs, and error conventions |
| [Deployment and operations](deployment-operations.md) | Raspberry Pi, Docker Compose, Traefik, Tailscale, configuration, backups, and observability |
| [Implementation plan](implementation-plan.md) | Milestones, implementation-agent packets, dependencies, release gates, Pi budgets, and Azure cost controls |
| [Product backlog](backlog.md) | Candidate features, design-risk mitigations, detailed delivery plans, priorities, and open product questions |

The source brief makes no framework choice. The documents therefore define stable boundaries and recommend a single deployable, server-rendered application with a relational database for the first release. Milestone 0 records the concrete framework/tooling choice before feature agents begin. This keeps the Pi deployment simple while allowing measured workload to move to a worker or consumption-based Azure function later.
