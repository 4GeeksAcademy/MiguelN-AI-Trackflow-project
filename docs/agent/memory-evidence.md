# Evidence: two complete memory cycles (MEM-092)

Quoted from the live recording in [`data/eval/agent/memory-traces.json`](../../data/eval/agent/memory-traces.json):
- recorded at 2026-11-13T04:58:02+00:00, graph `d0ac8a5a90bfbef3`;
- generation model `downtown-miami/openrouter/openai/gpt-6-luna`, embeddings `downtown-miami/openrouter/perplexity/pplx-embed-v1-0.6b`;
- Qdrant `trackflow_knowledge`, each case on a fresh memory store;
- the speaker is `eval:valentina.cruz`.

Each claim below is also an assertion in `npm run agent:memory:eval` (`tests/pipelines/test_agent_memory_evals.py`), so the document cannot silently drift from the recording. To reproduce it: `LLM_TIMEOUT_SECONDS=120 npm run agent:record -- --memory`, then the eval. The design is in [agent-memory.md](agent-memory.md).

## Cycle A: approved, and reflected in a future interaction

### Turn 1, conversation A: the user corrects a carrier rule

> **User:** Actually SEUR no longer covers that rural area of Zaragoza, we've had to use the local carrier since last month.

> **Agent:**
>
> Thanks for the update. The current documentation says SEUR has the best coverage in rural areas of Aragón and that local carriers are available only within Zaragoza metropolitan area. You report that since October 2026 SEUR no longer covers that rural Zaragoza area and your team has had to use a local carrier. This change is not reflected in the documentation; the local carrier’s coverage for that area should be confirmed with Carlos Vega, since undocumented carrier exceptions require his approval.
>
> Sources: carrier-coverage — Carrier Coverage › Spain (Zaragoza)
>
> Want me to remember this for next time? "The user reports that since October 2026, SEUR no longer covers rural Zaragoza, so the team has had to use a local carrier there." Reply yes, no, or tell me what to change.

- Nodes: `receive_question → recall_memory → plan_sources → retrieve → generate_answer → propose_memory → compose_response`
- Self-evaluation, in the same call as the answer: `memory_check = propose`, reason: "The user reports a change in SEUR coverage for rural Zaragoza and a resulting carrier switch, which is a corrected carrier rule."
- Proposal `9631c752…`: category `carrier_rule`, key `carrier_rule|seur|ES`, valid until 2027-05-12. **Pending; nothing is written yet** (active memories after the turn: 0).

### Turn 2, conversation A: the user approves

> **User:** Yes, please remember that.

> **Agent:** Saved to team memory: "The user reports that since October 2026, SEUR no longer covers rural Zaragoza, so the team has had to use a local carrier there." I'll use it in future conversations.

- Nodes: `receive_question → classify_memory_reply → resolve_memory_proposal → compose_response` (the pending proposal is evaluated first, and nothing else is asked)
- Explicit classification: `approve`, confidence 1.0, decision span "Yes, please remember that." (found in the message) → **approved**
- Written: memory `0aa756ba…`, approved by `eval:valentina.cruz` at 2026-11-13T04:53:25.830+00:00, from proposal `9631c752…`. Outcome `memory_reply`.

### Turn 3, a new conversation (B): the memory is used

> **User:** Which carrier should we use for deliveries to rural areas of Zaragoza?

> **Agent:**
>
> The documentation says SEUR has the best rural coverage in Aragón. However, team memory reports that since October 2026 SEUR no longer covers rural Zaragoza and the team has had to use a local carrier there. The documentation limits local carriers to the Zaragoza metropolitan area, so it does not confirm coverage for rural areas outside that area. The system assigns the carrier for each shipment; any exception requires approval from Carlos Vega.
>
> Sources: carrier-coverage — Carrier Coverage › Spain (Zaragoza); carrier-coverage — Carrier Coverage; team memory — carrier rule (SEUR, ES), approved 2026-11-13

- Nodes: `receive_question → recall_memory → plan_sources → retrieve → generate_answer → compose_response`
- `recall_memory` in the new conversation returned `0aa756ba…` (SEUR, ES, similarity, score 0.5551): the approved memory, and only it.
- The answer states the team memory next to the documentation, with its approval date, and cites it under "Sources".

### The decision log (append-only)

| # | Event | By | Decision | Message | Content | When |
|---|---|---|---|---|---|---|
| 1 | `proposed` | eval:valentina.cruz | — · This reported coverage change may affect carrier selection for shipments in rural Zaragoza. | Actually SEUR no longer covers that rural area of Zaragoza, we've had to use the local carrier since last month. | The user reports that since October 2026, SEUR no longer covers rural Zaragoza, so the team has had to use a local carrier there. | 2026-11-13T04:53:24.477+00:00 |
| 2 | `approved` | eval:valentina.cruz | approve 1.00 · the user approved it ("Yes, please remember that.") | Yes, please remember that. | The user reports that since October 2026, SEUR no longer covers rural Zaragoza, so the team has had to use a local carrier there. | 2026-11-13T04:53:25.830+00:00 |

## Cycle B: rejected, and memory stays unchanged

### Turn 1, conversation A: a client preference is proposed

> **User:** The cosmetics client always wants their monthly report with the returns breakdown first, before shipment volume.

> **Agent:**
>
> Understood—you report that the cosmetics client wants its monthly report to show the returns breakdown before shipment volume.
>
> Want me to remember this for next time? "The cosmetics client prefers its monthly report to show the returns breakdown before shipment volume." Reply yes, no, or tell me what to change.

- Self-evaluation: `propose`, reason: "The user reports a recurring client's monthly report format preference". Pending proposal: `client_report_preference|the-cosmetics-client`. Active memories: 0.

### Turn 2, conversation A: the user rejects

> **User:** No, don't save that, it was a one-off request.

> **Agent:** Understood: I won't remember it.

- Nodes: `receive_question → classify_memory_reply → resolve_memory_proposal → compose_response`
- Classification: `reject`, confidence 1.0, span "No, don't save that, it was a one-off request." → **rejected**. The user's explanation ("it was a one-off request") is part of the decision, not a new request.
- Memory after the turn: 0 active entries, the same as before. The proposal row stays, with status **`rejected`**, resolved at 2026-11-13T04:53:39.079+00:00.

### Turn 3, a new conversation (B): nothing was learned

> **User:** How does the cosmetics client want its monthly report?

> **Agent:**
>
> The knowledge base does not specify how the cosmetics client wants its monthly report formatted or what metrics it should highlight. Please confirm the preference with the responsible account manager. The documentation only says that an inventory aging report can be requested through the account manager; it does not state a monthly-report preference.
>
> Sources: storage-pricing — Storage Pricing

- `recall_memory`: 0 items ("the memory holds no active entries"). The rejected preference is not used; the store is unchanged (0 memories ever written).

### The decision log (append-only)

| # | Event | By | Decision | Message | Content | When |
|---|---|---|---|---|---|---|
| 1 | `proposed` | eval:valentina.cruz | — · This recurring report-format preference can guide future monthly reports. | The cosmetics client always wants their monthly report with the returns breakdown first, before shipment volume. | The cosmetics client prefers its monthly report to show the returns breakdown before shipment volume. | 2026-11-13T04:53:37.652+00:00 |
| 2 | `rejected` | eval:valentina.cruz | reject 1.00 · the user rejected it ("No, don't save that, it was a one-off request.") | No, don't save that, it was a one-off request. | The cosmetics client prefers its monthly report to show the returns breakdown before shipment volume. | 2026-11-13T04:53:39.079+00:00 |

## The other recorded cases

| Case | What the recording shows |
|---|---|
| `propose-carrier-correction` | briefing example 1 → `propose` (`carrier_rule`), asked in the response |
| `propose-recurring-incident` | briefing example 2 → `propose` (`recurring_incident`) |
| `propose-client-report-preference` | briefing example 3 → `propose` (`client_report_preference`); the planner marked the message as an update (`note_team_update`) |
| `no-proposal-one-off-tracking` | briefing counter-example 1 → no proposal |
| `no-proposal-closing` | briefing counter-example 2 → no proposal |
| `no-proposal-single-use-task` | briefing counter-example 3 → no proposal |
| `forbidden-b2c-address` | B2C address → no proposal; the address and postcode never reach the log |
| `forbidden-b2b-location` | B2B pickup site and gate code → no proposal (a fixed answer, no generation); nothing reaches the log |
| `forbidden-single-package-incident` | single package incident → `must_not_be_remembered` |
| `forbidden-contract-negotiation` | contract negotiation → `must_not_be_remembered` |
| `edit-then-confirm` | edit → asked again with the user's text → approved; the stored text mentions the PDF |
| `topic-change-discards` | change of topic → discarded; the new question answered normally |
| `unclear-reply-discards` | "maybe later" → `unclear` → discarded; nothing else answered |
| `answer-and-ask-in-one-message` | "Yes, save it. And what's the standard return window?" → approved, then answered (30 days) |
| `one-pending-proposal-at-a-time` | an edit pending confirmation → the new SEUR proposal in the same message is `suppressed` |
