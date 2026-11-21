# Gateway behavior coverage

Tracking issue: [#100](https://github.com/clawp-agents/clawp-workspace/issues/100).
Frontend behavior is excluded. This is a contract inventory, not a line-coverage
percentage target. Existing coverage below describes the baseline at `c7735a6`;
it does not imply that those components are exhaustively tested.

Delivery order: Session characterization PR → merge → construction seam
[#99](https://github.com/clawp-agents/clawp-workspace/issues/99) PR → merge →
comprehensive coverage PR. Keep intentional behavior fixes separate from test
additions, and clarify suspected defects with Marc before locking them in.

## Existing baseline (206 tests)

| Area | Existing evidence | Important remaining contracts |
| --- | --- | --- |
| Persistence | `tests/store/test_base.py`, `test_memory.py`, `test_session.py`: JSON/JSONL creation, read/write, filtering, corrupt/truncated data, upgrades/backups, directory ownership, reopening, per-session wrappers | Real message round trips through orchestration; storage failures while processing |
| Provider | `tests/test_provider.py`: streamed OpenRouter content/reasoning/tool parsing, refusals, finish reasons, malformed chunks and stream failures | Request construction/errors and Session continuation, cancellation and timeouts |
| Streaming/utilities | `tests/test_util.py`: streamable lists, publisher subscriptions/history/cleanup, value futures, TTL caching, dependency context lifecycles | Session-level publication/finalization ordering and cancellation/shutdown |
| Channels | `tests/channel/test_management.py`: pool acquisition/release and account configuration; `test_github.py`: progress-checker pagination, caching and failures | Router delivery/metadata, unread tracking/batching, channel lifecycle and assignment; broader GitHub behavior using local fakes |
| Agent/Session | No direct orchestration tests | See checklists below |
| Tools, messages, files, config/state, API | No dedicated suites | See Step 2 checklist |

## Step 1 — small Session characterization baseline

`gateway/tests/agent/test_session.py` uses public transactions, a scripted
provider, recording sender, fake tool client and real temporary JSONL storage.
Only the info-manager boundary is stubbed (no pending information messages).
No Agent construction, private-attribute replacement or external services.

- [x] Incoming user metadata precedes user content; offsets remain ordered;
  appending alone neither prompts the provider nor sends a reply.
- [x] Incoming system content does not get user metadata prepended.
- [x] Provider sees the existing context and available tool definitions.
- [x] Responses finalize and persist; content is sent in the active chat;
  empty/reasoning-only responses remain in history without being sent.
- [x] Tool arguments are decoded, result IDs correlate to calls, and the next
  provider request sees the agent call followed by its tool result.
- [x] Tool execution errors become correlated results and allow continuation.
- [x] Appending an agent message processes sending/tools without automatically
  requesting a continuation.

Persistence comparisons use the public JSON representation: serialized message
timestamps intentionally have millisecond precision, unlike in-memory instants.
This baseline does **not** yet prove incremental publication before finalization,
transaction concurrency, cancellation, shutdown or complete error recovery.

## Step 2 — after #99 merges

Unchecked entries are planned work, not claims of existing protection. Add test
references and split entries as contracts are verified. Reuse existing evidence
above rather than duplicating lower-level tests.

- [ ] Agent/repository: discovery/hatching, onboarding, assignment, active-chat
  switching, unread batching and lifecycle/shutdown (including failures).
- [ ] Compaction: summary/rollover ordering, history/subscription continuity,
  and coordination preventing writes to retired sessions.
- [ ] Session transactions: compound writes cannot interleave; completion,
  cancellation and shutdown unblock waiting work without leaking transactions.
- [ ] Session streams: subscribers observe incremental output before persistence;
  finalization, provider errors, cancellation and shutdown leave no hanging streams.
- [ ] Session continuation: multiple tools, malformed arguments, session-operation
  results, send/storage errors, request bounds and request/send timeouts.
- [ ] System channel: restricted switching and previous-chat restoration,
  including exceptional exits.
- [ ] Channels/router: routing, unread handling, metadata, assignment/environment
  and lifecycle contracts not already covered by pool/progress-checker tests.
- [ ] MCP/tools: client lifecycle/filtering, built-ins, session operations,
  save actions and shell adapter success/failure/cancellation at local boundaries.
- [ ] Messages: serialization, metadata, streamed part assembly and errors beyond
  the provider parser coverage.
- [ ] Info/templates/personality: required ordering, deduplication, file updates,
  missing/invalid resources and rendered content.
- [ ] Config/state: validation, persistence and failure paths.
- [ ] REST/WebSocket: observable responses, validation/not-found errors,
  history/live-stream ordering and disconnect cleanup.
- [ ] Cross-component wiring: real core collaborators with local boundary fakes
  agree on routing, ordering and lifecycle behavior.

Use explicit async synchronization for concurrency tests, not wall-clock sleeps.
No live LLM, GitHub, Matrix, SSH or filesystem MCP server is required.

## Running

From the repository root (Python 3.14+ and uv):

```sh
uv --project gateway sync --extra test
uv --project gateway run pytest gateway
```

Step 1 result: **214 passed**, with the pre-existing async-solipsism
`DefaultEventLoopPolicy` deprecation warning.
