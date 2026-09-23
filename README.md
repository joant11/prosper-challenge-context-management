# Prosper Challenge — Context Management

Voice AI for healthcare scheduling. An agent is a **graph of nodes** (Pipecat Flows), defined declaratively as JSON and compiled into a runnable voice pipeline.

- **Phase 1** — a UI to build the node graph and place a test call. ✅ implemented — see below.
- **Phase 2** — a context-management approach so a scheduling agent can reliably and cost-effectively navigate a large catalog of locations, doctors, and appointment types.

```
browser mic  ->  ElevenLabs STT  ->  OpenAI LLM  ->  ElevenLabs TTS  ->  browser
```

## Quickstart

Requires **Python 3.11+**. Run from the repo root:

```bash
make install
make run
```

Open `http://localhost:8000`, create an agent (from scratch or from the clinic-scheduler template), edit its graph, save, and click **Start test call**. `Ctrl+C` to stop. (`make help` lists all targets.)\
\
Remember to update the `.env` file accordingly.

To run `example_flow.json` directly through Pipecat's own dev runner instead (no builder UI, just the prebuilt WebRTC client), use `make run-bot` and open `http://localhost:7860/client`.

## Layout

| Path | Responsibility |
| --- | --- |
| `backend/bot.py` | The voice pipeline (WebRTC + ElevenLabs STT/TTS + OpenAI LLM). `run_bot()` loads an agent (an `AgentBuilder`) and runs it; both `bot.py`'s own dev-runner entry point and `server.py`'s test-call endpoint reuse it. |
| `backend/agent_builder/` | All agent-building code. `schema.py` = the declarative `AgentConfig` / `Node` / `Edge` contract; `validation.py` = graph checks (dangling edges, unreachable nodes, ...); `builder.py` = `AgentBuilder`, which validates the JSON and compiles it into a Pipecat Flows graph. |
| `backend/agent_store.py` | Agent persistence — one JSON file per agent under `backend/data/agents/`, plus the "blank" and "clinic scheduler" starter templates. |
| `backend/server.py` | The Phase 1 web app: agent CRUD API, WebRTC test-call signaling (`/api/offer`), and the static frontend. Entry point for `make run`. |
| `frontend/` | The builder UI — a small vanilla HTML/CSS/JS single-page app (no build step): agent list, a drag-and-connect node/edge canvas, a node inspector, live validation, and the test-call button. Served by `backend/server.py`. |
| `backend/example_flow.json` | The original example agent **as data** — a clinic scheduler. Still runnable standalone via `make run-bot`; untouched by the builder work. |
| `backend/data/catalog.json` | A deliberately large, deliberately messy clinic catalog (locations, providers, appointment types, booking rules) for the Phase 2 work. See [`backend/data/README.md`](backend/data/README.md). |

## The agent schema

A node has a `type` (`message` / `collect` / `decision` / `tool_call` / `end`) — purely a UI/authoring hint, since `AgentBuilder` compiles every node the same way from `edges` and `end` regardless of `type`. Two fields exist for Phase 2 to build on without another schema migration:

- `data_refs` on a node — reserved for structured catalog references (e.g. `{"appointment_type": "appt_002"}`) instead of free text. Unused by the builder in Phase 1.
- `tool_call` nodes can carry a `catalog_call` dict (`{"function": "<backend/catalog.py function>", "args": {...}}`), merged into the conversation state when their edge fires — this is how a node makes a real catalog lookup. The "Presentation Prosper" template demonstrates this end to end.

Existing JSON without these fields (like `example_flow.json`) still loads: `type` is inferred from the node's shape and the rest default to empty.