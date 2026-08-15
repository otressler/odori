# Odori documentation

| Document | Purpose |
| --- | --- |
| [Product requirements](product-requirements.md) | Scope, user journeys, requirements, acceptance criteria, collaboration, and delivery phases |
| [Architecture](architecture.md) | System context, components, integrations, workflows, real-time collaboration, and technology guidance |
| [Domain model](domain-model.md) | Data entities, relationships, lifecycle rules, and inventory semantics |
| [API specification](api-specification.md) | HTTP resources, asynchronous jobs, and error conventions |
| [Deployment and operations](deployment-operations.md) | Raspberry Pi, Docker Compose, Traefik, Tailscale, configuration, backups, and observability |
| [Developer onboarding](development.md) | Local setup, Google sign-in, and currently built AI integrations |
| [Implementation plan](implementation-plan.md) | Milestones, implementation-agent packets, dependencies, release gates, Pi budgets, and Azure cost controls |
| [Product backlog](backlog.md) | Candidate features, design-risk mitigations, detailed delivery plans, priorities, and open product questions |

Milestone 0 selected and established a Django modular monolith with server-rendered templates, a relational database, and a separate worker process. The documents define the implemented contracts and clearly label later work, keeping the Pi deployment simple while allowing measured workload to move to an Azure Function only when justified.
