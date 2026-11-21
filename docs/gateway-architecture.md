# Gateway architecture

This document describes the backend in `gateway/`. It is a map of the current
implementation, not a proposal for a future design. The web application in
`web/` is outside its scope.

## Purpose and architectural shape

The gateway is a long-running, asynchronous Python process that hosts multiple
AI agents. It connects each agent to:

- an LLM provider;
- communication channels such as the web UI, GitHub, Matrix, and other agents;
- a filtered set of MCP tools, including workspace files and a sandboxed shell;
- durable, filesystem-backed sessions, channel progress, and memories; and
- a FastAPI management and streaming API.

The central runtime hierarchy is:

```text
Gateway process
├── GatewayStateManager
├── OpenrouterProvider (shared)
├── ChannelPool (configured external accounts)
├── AgentRepository
│   └── Agent (one per persisted agent directory)
│       ├── ChannelRouter
│       │   ├── built-in channels: system, web_ui, agent
│       │   └── at most one assigned external channel of each type
│       ├── Client (per-agent MCP tool composition)
│       ├── SessionsStore and MemoryStore
│       └── Session (one active context window, older sessions retained)
└── Api (FastAPI/Uvicorn)
```

`Agent` is the main orchestration boundary. `Session` owns context-window
behavior, while channels, tools, providers, stores, and API handlers are
adapters around it. Pydantic models in `clawp.model` form the serialized
contracts between these parts.

## Source map

| Path | Responsibility |
| --- | --- |
| `main.py` | Process bootstrap, lifecycle ordering, signal-based shutdown |
| `config.py`, `model/config.py` | YAML loading, validation, secret resolution, derived paths |
| `state.py`, `model/state.py` | Gateway-wide and agent/session mutable state |
| `agent/agent.py` | Agent lifecycle, channel coordination, session continuity, compaction |
| `agent/error.py` | Agent-related exception classes |
| `agent/session.py` | Session context, transactions, streaming, provider/tool response loop |
| `agent/repository.py` | Agent discovery, construction, hatching, and repository lifecycle |
| `message.py` | Runtime and streaming message objects |
| `provider.py` | LLM-provider boundary and OpenRouter stream translation |
| `channel/` | Channel contract, account pool, per-agent routing, channel adapters |
| `tool/` | Per-agent MCP composition and built-in/filesystem/shell tools |
| `store/` | JSONL persistence for sessions, channel messages, and memories |
| `file/` | Packaged personalities, templates, tutorials, and info-message assembly |
| `model/` | Pydantic configuration, API, persistence, and wire models |
| `api/` | REST management endpoints and bidirectional WebSocket streaming |

A useful distinction is that `model/` contains materialized data used at
serialization boundaries, while modules such as `message.py` contain live
runtime objects. For example, a streaming `message.AgentMessage` becomes a
`model.AgentMessage` only when its stream has finalized.

## Startup and shutdown

`clawp.main.run()` loads and validates configuration, applies the configured
process umask, and enters the async runtime. `main()` then constructs components
and enters them in dependency order:

1. Load or initialize gateway state.
2. Construct the configured external `ChannelPool`.
3. Start the shared OpenRouter provider.
4. Discover and start persisted agents in `AgentRepository`.
5. Start Uvicorn through `Api`.
6. Wait for `SIGTERM`.

An `AsyncExitStack` unwinds runtime components in reverse order. The state
manager is a separate context manager entered after the stack, so it exits
first and writes `gateway_state.json` before the stack stops the API, agent
repository, and provider. This prioritizes persisting global state even if a
later component has trouble shutting down. Each repository agent similarly
owns the lifecycle of its store, tools, channels, active session, and
unread-message task.

## Agent aggregate and persistence

`AgentRepository` treats each directory below `<files_base_dir>/agents/` as an
agent aggregate. It discovers agents on startup and creates new aggregates when
an agent is hatched.

```text
agents/<agent UUID>/
├── agent_information.json   # immutable identity and personality metadata
├── agent_state.json         # active chat, channel claims, tools, read offsets
├── workspace/               # agent-controlled files and personality files
├── sessions/
│   └── <sequence>/
│       ├── messages.jsonl   # append-only context, with a versioned header
│       └── state.json       # info messages shown in this session
├── memory_store/memory.jsonl
├── web_ui_channel/messages.jsonl
└── agent_channel/<peer UUID>.jsonl
```

Global channel progress that belongs to an external account, currently GitHub
read markers and unread messages, lives in `gateway_state.json`. Matrix keeps
its SDK state below the configured gateway files directory.

The persistence strategy is intentionally simple: Pydantic JSON and versioned
JSONL files. `JsonlIO` serializes access, flushes and `fsync`s appends, and can
repair a corrupt final line after an unclean shutdown. `SessionsStore` verifies
contiguous session numbering, exclusively claims its directory within the
process, and provides a format-upgrade mechanism. Most mutable state files are
written on orderly component shutdown; append-only message records are written
as messages complete.

## Sessions, messages, and the response loop

A session is one append-only model context window. An agent keeps prior
sessions readable but has exactly one active session. Changing history is done
by starting a new session rather than rewriting an existing one.

`SessionTransaction` serializes compound writes within a session so metadata,
messages, model responses, and tool results cannot interleave incorrectly.
`Agent._active_session_lock` is a coarser lock used when an operation may replace
the active session, notably compaction. The special
`SystemChannelSessionTransaction` temporarily pins output to the system channel
and forbids chat switching.

### Incoming message flow

```text
channel adapter
  -> publishes IncomingMessage
  -> ChannelRouter multiplexes channels
  -> Agent unread-message worker acquires active session transaction
  -> appends metadata + user/system message
  -> Session requests a provider response
```

If a message arrives in the active chat, it is appended directly. If it arrives
in another chat, the agent is shown an unread overview and the gateway injects
an artificial `switch_chat` tool call. This reuses the normal tool semantics to
change the active chat and append its unread messages.

Channels own unread/read semantics. Calling `get_unread_messages()` consumes
messages and updates the adapter's read marker. Adapters also prevent outgoing
messages while unread input remains, preserving conversational ordering.

### Provider and tool loop

For one requested response, `Session`:

1. asks the channel router for outgoing metadata for the active chat;
2. starts an empty, streamable agent message;
3. sends the current session and enabled MCP schemas to the provider;
4. publishes stream parts immediately to subscribers while accumulating them;
5. waits for finalization and appends the complete message to durable storage;
6. sends non-empty content through the selected channel;
7. executes tool calls in order and appends a tool message for each result; and
8. asks the provider again when tool results or a send error require another
   turn.

The configured doom-loop limit bounds these successive provider requests.
Timeouts independently bound a provider turn and outgoing channel delivery.

`OpenrouterProvider` is the provider implementation. It translates runtime
messages and MCP schemas to OpenRouter requests, then converts event-stream
chunks into typed reasoning, content, tool, and error parts. The abstract
`Provider` contract keeps provider-specific transport out of session
orchestration.

### Streaming model

Agent messages are nested streams: a message contains ordered parts, and each
part contains fragments. `StreamableList` supports this producer/consumer
shape and has an explicit finalization boundary. Message timestamps that are
unknown at stream start are represented as future values and resolved at
finalization.

`Publisher` provides in-process fan-out for channel events and session message
subscriptions. The API maps the nested agent-message stream to a stateful
WebSocket protocol with message-start/end and part-start/end markers. Other
message roles are sent as complete models.

### Compaction

Compaction does not summarize messages inside the gateway. It temporarily
switches the agent to the internal system channel, asks the model to produce a
summary, closes the active session, and creates the next session. Standard
onboarding information and the model-authored summary seed the new context.
Old sessions remain persisted and visible through history APIs.

## Channels

`Channel` defines the adapter contract:

- lifecycle (`start`/`stop`);
- identity and status;
- chat descriptor validation and enrichment;
- unread-message retrieval;
- outgoing metadata construction and sending;
- optional shell environment and required tools; and
- informational messages/tutorials.

Two managers operate at different scopes:

- `ChannelPool` owns configured external accounts and enforces exclusive
  assignment to agents.
- `ChannelRouter` owns one agent's active adapters, multiplexes their incoming
  streams, and routes outgoing messages by `ChatDescriptor.channel`.

Every agent receives built-in `system`, `web_ui`, and `agent` channels.
Configured GitHub and Matrix accounts can be assigned at runtime. A router
allows at most one channel instance of each type, and agent state persists its
external claims.

Channel-specific complexity remains in adapters. For example, GitHub polls API
endpoints, uses ETags plus durable event markers, presents labeled issues and
PRs as chats, and injects short-lived credentials into the shell environment.
Matrix wraps `matrix-nio`; web UI and agent-to-agent channels use local JSONL
logs.

## Tools and workspace isolation

Each agent gets one `tool.Client`. It composes local FastMCP servers into a
single in-process MCP endpoint and filters the resulting tool schemas using the
agent's persisted include/exclude/options specification.

The composition currently includes:

- Clawp tools for tutorials, chat switching, and optional memory;
- a proxy to `rust-mcp-filesystem`, restricted to the agent workspace by MCP
  roots; and
- a shell tool that connects over SSH to the separately deployed sandbox.

Some tools need to mutate the current session after returning. Tool results
cannot serialize a Python closure, so `ComplexToolResultMetadataRegistry`
stores such process-local metadata behind an opaque result ID. The session
recognizes `SessionOperationToolResult` and executes its operation while still
inside the current transaction. `switch_chat` uses this mechanism to append the
new chat's unread messages after its visible tool result.

The shell server obtains fresh environment additions from channels on every
call, allowing credentials such as GitHub installation tokens to be short
lived. The external command wrapper is the security boundary that maps an
agent to an OS user and workspace. The gateway-side MCP filesystem root is a
second boundary. Optional save actions deliberately execute agent-controlled
commands after file writes and therefore run through the same sandboxed shell.

## Informational context and personalities

Agent behavior is partly data-driven. Packaged personality manifests select
initial workspace files and tool policy. `InfoProvider` implementations on the
agent, personality, channels, and MCP servers declare information the model
must receive. `InfoManager` resolves those declarations into ordered developer
or system messages and records which were shown in the current session.

This makes feature onboarding compositional: assigning a channel or enabling a
tool can add its tutorial or config-file content without central session code
knowing the details. Starting a new session re-establishes this context.

## HTTP API boundary

FastAPI handlers are thin adapters over repositories and agents:

- agent listing, hatching, history, compaction, and live messaging;
- external channel listing and assignment; and
- personality discovery.

Long-lived WebSockets both subscribe to agent messages and accept web UI user
input. Runtime dependencies are installed on `app.state` and resolved through
FastAPI dependency functions. The API does not own domain state or agent
lifecycles.
