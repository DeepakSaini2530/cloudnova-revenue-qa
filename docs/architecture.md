# Architecture

What is actually built, end to end. Every box maps to a file.

```mermaid
flowchart TD
    subgraph BUILD["Build: app/pipeline.py (full refresh on every run, under 1s)"]
        CSV[("data/cloudnova_invoices.csv<br/>5,125 dirty rows")]
        CLEAN["Row cleaning<br/>strict category maps, 5 date shapes,<br/>strict amounts and integers"]
        DEDUP["Quarantine contradictory copies,<br/>then one row per invoice_id<br/>(latest lifecycle status wins), sign rule"]
        DERIVE["Derive<br/>amount_usd (FX), mrr_usd, recognized_usd"]
        CSV --> CLEAN --> DEDUP --> DERIVE
    end

    subgraph STORE["Store: build/cloudnova.duckdb"]
        INV[("invoices<br/>5,000 rows")]
        ACC[("accounts<br/>3,815 rows, latest invoice wins")]
        CON[("contacts<br/>emails, not readable by Ask")]
        REJ[("rejects<br/>row + reason, 0 for this file")]
    end

    DERIVE --> INV --> ACC
    DERIVE --> CON
    CLEAN -- "unmapped or unparseable" --> REJ
    DEDUP -- "contradictory copies,<br/>sign mismatch" --> REJ

    subgraph ASK["Ask: app/ask.py"]
        Q["Question<br/>3 to 500 characters"]
        PROMPT["System prompt =<br/>specs/02-metrics.md + live schema"]
        LLM["Claude Code CLI, headless<br/>no tools, question on stdin"]
        GUARD{"Parse tree: one SELECT<br/>over invoices and<br/>accounts only?"}
        EXEC["Execute<br/>read-only, file access off,<br/>1 GB, 10s limit, max 50 rows"]
        OUT["answered:<br/>SQL + rows + explanation<br/>+ 'Check first' caveats from the<br/>comments of the columns used"]
        REFUSED["refused:<br/>reason shown"]
        FAILED["failed:<br/>which step, no partial answer"]
        Q --> LLM
        PROMPT --> LLM
        LLM -- "sql + explanation" --> GUARD
        LLM -- "sql: null" --> REFUSED
        GUARD -- yes --> EXEC --> OUT
        GUARD -- "no, bad JSON, DB error or timeout:<br/>retry once with the error" --> LLM
        GUARD -- "second failure" --> FAILED
    end

    INV --> EXEC
    ACC --> EXEC

    subgraph EVALS["Evals: python -m evals"]
        REF["evals/reference.py<br/>stdlib recompute from the raw CSV"]
        OFF["Offline: invariants, synthetic reject paths,<br/>reference vs SQL, guard bypass attempts, caveats"]
        NL["Live: 8 questions through Ask, N runs each,<br/>rows checked against the reference"]
        REF --> OFF
        REF --> NL
    end

    CSV --> REF
    INV --> OFF
    OUT --> NL
    CI["GitHub Action<br/>runs the offline layer on every push"] --> OFF
```

## Reading it

- **One direction of data flow.** Raw CSV to cleaned tables to answers. The question layer can only
  read `invoices` and `accounts`; it cannot write, reach the raw file, or read `contacts` and `rejects`.
- **Business rules sit in the store, not in the prompt.** `amount_usd`, `recognized_usd` and `mrr_usd`
  are columns, so the model's job is to pick and aggregate columns, not to re-derive revenue rules.
- **Warnings travel with the data.** A column or table comment can end in `Caveat: ...`. The guard
  already knows which columns a query touched, so those caveats are printed under the answer whatever
  the model wrote.
- **One glossary.** `specs/02-metrics.md` is read by people, drove the build, and is sent to the model
  verbatim. There is no second copy to drift.
- **Two independent implementations of the answers.** The SQL pipeline and `evals/reference.py` share
  no code. The evals pass only when they agree.
