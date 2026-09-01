# C4 Level 1 — System Context

Who uses the platform, what sends it events, and what it depends on.

```mermaid
flowchart TB
    subgraph customers[" "]
        direction LR
        VISITOR["Website visitor<br/><i>anonymous, later known</i>"]
        ANALYST["Marketing analyst<br/><i>nonprofit / association staff</i>"]
        OPERATOR["Platform operator<br/><i>on-call engineer</i>"]
    end

    subgraph senders[" "]
        direction LR
        TAG["Website tag<br/><i>pageviews, clicks, forms</i>"]
        EMAIL["Email platform<br/><i>opens, clicks</i>"]
        ADS["Ad platform<br/><i>impressions, clicks</i>"]
        CRM["CRM / donation forms<br/><i>registrations, gifts</i>"]
    end

    PLATFORM["<b>Event Processing Platform</b><br/><br/>Ingests marketing events asynchronously,<br/>stores them immutably per tenant,<br/>serves queries, aggregates, search,<br/>and cached live summaries"]

    VISITOR -->|"browses, converts"| TAG
    TAG -->|"POST /events<br/>202 Accepted"| PLATFORM
    EMAIL -->|"POST /events"| PLATFORM
    ADS -->|"POST /events"| PLATFORM
    CRM -->|"POST /events"| PLATFORM

    ANALYST -->|"GET /events, /stats,<br/>/search, /stats/realtime"| PLATFORM
    OPERATOR -->|"GET /metrics, /readyz<br/>replays dead letters"| PLATFORM

    classDef system fill:#1a4d7a,stroke:#0d2c47,color:#fff
    classDef person fill:#5a7a99,stroke:#3d5266,color:#fff
    classDef ext fill:#8899aa,stroke:#5a6a7a,color:#fff
    class PLATFORM system
    class VISITOR,ANALYST,OPERATOR person
    class TAG,EMAIL,ADS,CRM ext
```

## Boundaries worth stating

**Every sender is untrusted.** Events arrive over the public internet from tags a customer
installed on their own site. Payloads are schema-validated, size-bounded, and depth-bounded
before they enter the queue.

**Tenant comes from the credential, never the payload.** Each sender holds an opaque API
key mapped server-side to exactly one organisation (ADR-004). No request field names a
tenant.

**Out of scope, deliberately.** No frontend. No real ad-network or CRM integration — those
are event sources, not systems we call. No identity-resolution graph joining anonymous
visitors to known contacts; the platform stores both identifiers faithfully and leaves the
join to a downstream system.
