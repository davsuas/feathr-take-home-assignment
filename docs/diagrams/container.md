# C4 Level 2 — Containers

What runs, what stores state, and which arrows are on the critical path.

```mermaid
flowchart TB
    SENDER["Event sender<br/><i>tag, email, ads, CRM</i>"]
    ANALYST["Analyst / dashboard"]

    subgraph platform["Event Processing Platform"]
        direction TB
        API["<b>API</b><br/>FastAPI + Uvicorn<br/><i>validate, resolve tenant,<br/>enqueue, serve reads</i>"]
        WORKER["<b>Worker</b><br/>Python asyncio<br/><i>consume, persist idempotently,<br/>project, reconcile</i>"]
        QUEUE[["<b>Queue</b><br/>in-process (default)<br/>or RabbitMQ (compose)"]]
    end

    MONGO[("<b>MongoDB</b><br/>CANONICAL<br/><i>events, dead letters,<br/>tenants, credentials</i>")]
    ES[("<b>Elasticsearch</b><br/>derived<br/><i>search projection</i>")]
    REDIS[("<b>Redis</b><br/>derived<br/><i>cached summaries, locks</i>")]

    SENDER -->|"POST /events"| API
    API -->|"1. publish<br/><i>bounded, timed out</i>"| QUEUE
    API -.->|"202 Accepted<br/><b>not yet stored</b>"| SENDER

    QUEUE -->|"2. receive"| WORKER
    WORKER -->|"3. insert<br/><i>unique (tenant_id, event_id)</i>"| MONGO
    WORKER -->|"4. ack<br/><i>only after 3 succeeds</i>"| QUEUE
    WORKER -->|"5. project<br/><i>after ack; failure is recoverable</i>"| ES
    WORKER -->|"6. reconcile pending"| MONGO

    ANALYST -->|"GET /events, /stats"| API
    API -->|"read"| MONGO
    ANALYST -->|"GET /events/search"| API
    API -->|"structured query<br/><i>tenant filter added here</i>"| ES
    ANALYST -->|"GET /stats/realtime"| API
    API -->|"cache-aside, 30s TTL"| REDIS
    API -.->|"on cache failure:<br/>compute, mark degraded"| MONGO

    classDef app fill:#1a4d7a,stroke:#0d2c47,color:#fff
    classDef canonical fill:#2d6a4f,stroke:#1b4332,color:#fff
    classDef derived fill:#7a6a1a,stroke:#4d4210,color:#fff
    classDef queue fill:#5a4a7a,stroke:#3a2f52,color:#fff
    classDef ext fill:#8899aa,stroke:#5a6a7a,color:#fff
    class API,WORKER app
    class MONGO canonical
    class ES,REDIS derived
    class QUEUE queue
    class SENDER,ANALYST ext
```

## Ordering that is load-bearing

**Step 4 after step 3, never before.** The queue message is acknowledged only once the
canonical record is confirmed stored. Reversing these loses events on a worker crash.

**Step 5 after step 4, never before.** Projection happens after acknowledgement, so an
Elasticsearch outage delays search without stalling ingestion (ADR-003). Step 6 is the
recovery path for step 5 failing (ADR-005).

**The dotted 202 is the whole point of the design.** It returns before steps 3–5 have
happened, which is why the response says `accepted` and the API description states plainly
that it does not mean stored.

## Colour key

Green is canonical and cannot be rebuilt. Amber is derived: both stores can be dropped and
rebuilt from MongoDB, and the test suite does exactly that between runs.
