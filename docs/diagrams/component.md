# C4 Level 3 — Components

The hexagonal boundary, as it exists in the directory tree.

```mermaid
flowchart TB
    subgraph entry["Entry points"]
        direction LR
        ROUTES["api/routes/<br/><i>events, stats, search,<br/>dead_letters, health</i>"]
        MW["api/middleware/<br/><i>correlation, rate_limit</i>"]
        DEPS["api/dependencies.py<br/><i>credential → TenantContext</i>"]
        CONSUMER["worker/consumer.py<br/><i>the ack-after-persist loop</i>"]
        RECON["worker/reconciler.py<br/><i>repairs failed projections</i>"]
    end

    COMP["composition.py<br/><i>the only module that knows<br/>which adapter is mounted</i>"]

    subgraph app["application/ — use cases"]
        direction LR
        INGEST["ingest_event"]
        PROCESS["process_event"]
        QUERY["query_events"]
        AGG["aggregate_stats"]
        SEARCH["search_events"]
        RT["realtime_stats"]
        REPLAY["replay_dead_letter"]
    end

    subgraph ports["ports/ — Protocols"]
        direction LR
        P1["EventQueue"]
        P2["EventRepository<br/><i>no update, no delete</i>"]
        P3["EventSearchIndex"]
        P4["StatsCache"]
        P5["DeadLetterStore"]
        P6["TenantDirectory"]
        P7["Clock"]
        P8["Metrics"]
    end

    subgraph domain["domain/ — imports nothing"]
        direction LR
        EVENT["event, event_type,<br/>metadata, identity, errors"]
        POL["policies/<br/><i>retry, cache_key,<br/>time_window, idempotency</i>"]
    end

    subgraph infra["infrastructure/ — adapters"]
        direction LR
        A1["queue/<br/>in_memory · rabbitmq"]
        A2["persistence/mongo/<br/>repository · dead_letters<br/>tenants · indexes"]
        A3["search/elasticsearch/<br/>index · mappings<br/>query_builder"]
        A4["cache/redis/<br/>stats_cache · keys"]
        A5["observability/<br/>logging · metrics<br/>correlation"]
    end

    ROUTES --> DEPS
    MW --> ROUTES
    ROUTES --> app
    CONSUMER --> PROCESS
    RECON --> P2
    RECON --> P3
    COMP -.->|"wires at startup"| app
    COMP -.-> infra

    app --> ports
    app --> domain
    ports --> domain
    infra -.->|"implements"| ports

    classDef entrycls fill:#1a4d7a,stroke:#0d2c47,color:#fff
    classDef appcls fill:#2d6a4f,stroke:#1b4332,color:#fff
    classDef portcls fill:#5a4a7a,stroke:#3a2f52,color:#fff
    classDef domcls fill:#7a3a3a,stroke:#4d2020,color:#fff
    classDef infracls fill:#7a6a1a,stroke:#4d4210,color:#fff
    class ROUTES,MW,DEPS,CONSUMER,RECON entrycls
    class INGEST,PROCESS,QUERY,AGG,SEARCH,RT,REPLAY appcls
    class P1,P2,P3,P4,P5,P6,P7,P8 portcls
    class EVENT,POL domcls
    class A1,A2,A3,A4,A5 infracls
```

## The rule, and what enforces it

Every arrow points inward. `domain` imports nothing — not FastAPI, not a driver, not even
`eventplatform.infrastructure`. `application` reaches infrastructure only through
`ports`, which are `Protocol`s, so an adapter never imports the port it satisfies.

This is checked, not asserted. `.importlinter` defines two contracts and
`tests/architecture/test_boundaries.py` runs them plus an AST scan. Both fail the build on
violation. During implementation the contract caught `worker/main.py` importing the
composition root from `api/dependencies.py`; `composition.py` exists at the package root
because of that finding.

## Two structural details that carry guarantees

**`EventRepository` has no `update` and no `delete`.** Event immutability is enforced by
the method not existing, rather than by a rule someone must remember.
`update_projection` is the single narrow exception and touches only projection state.

**Every tenant-touching port method takes `tenant: TenantContext` first.** No ambient
context, no default. A forgotten scope is a type error at authoring time (ADR-004).
