# Ingestion and Failure/Retry Sequences

## Success path

```mermaid
sequenceDiagram
    autonumber
    participant S as Sender
    participant A as API
    participant Q as Queue
    participant W as Worker
    participant M as MongoDB
    participant E as Elasticsearch

    S->>A: POST /events (Bearer key)
    A->>A: resolve tenant from credential hash
    Note over A: caller cannot name a tenant
    A->>A: validate schema, metadata limits,<br/>occurred_at window
    A->>Q: publish (bounded, timed out)
    A-->>S: 202 Accepted {event_id, event_id_origin}
    Note over S,A: NOT stored yet. Not searchable yet.

    Q->>W: receive
    W->>M: insert_one (unique tenant_id+event_id)
    M-->>W: INSERTED
    W->>Q: acknowledge
    Note over W,Q: ack strictly after persistence
    W->>E: index (after ack)
    W->>M: projection.status = indexed
```

## Duplicate delivery

```mermaid
sequenceDiagram
    autonumber
    participant Q as Queue
    participant W as Worker
    participant M as MongoDB

    Q->>W: receive (redelivery of a stored event)
    W->>M: insert_one
    M-->>W: DuplicateKeyError
    W->>M: read stored content_hash
    alt hash matches
        W->>W: DUPLICATE_SUPPRESSED, counter++
    else hash differs
        W->>W: DUPLICATE_CONFLICT, first write kept
    end
    W->>Q: acknowledge
    Note over W,Q: all outcomes ack — the event is<br/>durably resolved in every case
```

## MongoDB unavailable: retry, then dead-letter

```mermaid
sequenceDiagram
    autonumber
    participant Q as Queue
    participant W as Worker
    participant M as MongoDB
    participant D as Dead-letter store

    Q->>W: receive (attempt n)
    W->>M: insert_one
    M--xW: ConnectionError
    Note over W: NOT acknowledged
    alt attempts remain
        W->>W: delay = min(1s·2^(n-1), 60s) with full jitter
        W->>Q: retry_later(delay)
        Note over W,Q: fire-and-forget — holding the message<br/>would stall healthy work behind it
    else attempts exhausted
        W->>D: record payload + reason + attempts + timings
        W->>Q: acknowledge
        Note over D: replayable, never silently dropped
    end
```

## Elasticsearch unavailable: the event survives

```mermaid
sequenceDiagram
    autonumber
    participant W as Worker
    participant M as MongoDB
    participant E as Elasticsearch
    participant R as Reconciler

    W->>M: insert_one
    M-->>W: INSERTED
    W->>E: index
    E--xW: SearchUnavailable
    W->>M: projection.status = failed
    Note over W,M: event remains fully retrievable<br/>via GET /events

    loop every 15s
        R->>M: find status in (pending, failed)
        R->>E: re-index
        alt Elasticsearch back
            E-->>R: ok
            R->>M: projection.status = indexed
        else still down
            Note over R: leave the marker, try next tick
        end
    end
```

## Poison message

```mermaid
sequenceDiagram
    autonumber
    participant Q as Queue
    participant W as Worker
    participant D as Dead-letter store

    Q->>W: receive (unparseable payload)
    W->>W: build_event → ValidationError
    W->>D: record (failure_class = validation)
    W->>Q: acknowledge
    Note over W,Q: dead-lettered at once, not retried —<br/>a message that cannot parse never will,<br/>and retrying it blocks the batch behind it
```

## Cache miss, hit, and failure

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant A as API
    participant R as Redis
    participant M as MongoDB

    C->>A: GET /events/stats/realtime
    A->>R: GET v1:{tenant}:stats:rt:{hash}
    alt hit
        R-->>A: value
        A-->>C: 200 {cached: true, age_seconds: n}
    else miss
        R-->>A: nil
        A->>M: aggregate
        A->>R: SET NX lock, then SET value EX 30
        A-->>C: 200 {cached: false, age_seconds: 0}
    else Redis unreachable
        R--xA: CacheUnavailable
        A->>M: aggregate
        A-->>C: 200 {degraded: true}
        Note over A,C: still a correct answer —<br/>a derived store never fails the request
    end
```
