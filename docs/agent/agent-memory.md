# Agent memory: learning from interaction, with the user's consent (MEM-092)

Ticket **MEM-092** (Milestone 8, Part 1). The rules come from [`memory-briefing.md`](../../memory-briefing.md). The agent is the same LangGraph agent as before ([agent-graph.md](agent-graph.md)): it reads tickets and stock through the MCP server, and the knowledge base through RAG.

## The problem

Every conversation started from zero. Valentina Cruz's 15 CX agents corrected the assistant on the same carrier rules again and again. It did not remember that an escalation had already been explained, or that someone had corrected a fact last week.

The fix is not a new agent. It is **one extra self-evaluation step** in the same graph:

1. While answering, the agent judges whether the message holds something worth remembering.
2. If it does, it **proposes** it inside its own response.
3. The user's next message is **classified** against that proposal.
4. Only an **approved and logged** proposal is written to memory, under the briefing's rules.

## The graph, extended

```
START -> receive_question
           --route_question--> END                                    (empty question)
           --route_question--> classify_memory_reply       (a proposal is pending)
           --route_question--> recall_memory
classify_memory_reply -> resolve_memory_proposal
           --route_after_memory_reply--> recall_memory (the message says more) | compose_response
recall_memory -> plan_sources -> [lookup_ticket_mcp] -> [lookup_stock_mcp] -> [retrieve]
          (unchanged)            -> generate_answer | answer_lookup_failed | answer_no_information
generate_answer --route_after_answer--> propose_memory | compose_response
propose_memory, answer_lookup_failed, answer_no_information -> compose_response -> END
```

| Node | What it does | Model? |
| --- | --- | --- |
| `classify_memory_reply` | Labels the message against the pending proposal (approve / reject / edit / unclear / unrelated). | yes, one structured call |
| `resolve_memory_proposal` | Applies the decision in one transaction and logs it. | no |
| `recall_memory` | Reads the approved memories this question needs: at most 3. | embeddings only |
| `generate_answer` | Gives the answer **and** the self-evaluation, in one structured call. | yes (was already) |
| `propose_memory` | Offers the proposal after the policy checks. It never writes a memory. | no |
| `compose_response` | Single exit. Adds the fixed memory texts around the answer. | no |

None of the Part 2 routing decisions changed. With nothing remembered and nothing pending, the evals strip out the memory nodes and compare the rest with `routing-baseline.json`; the run must be identical.

## 1. Memory architecture

### Backend: SQLite, in its own file

`AGENT_MEMORY_PATH` defaults to `data/agent/agent_memory.sqlite`, which is git-ignored; Compose uses `/app/var/agent_memory.sqlite`. The code is in [`services/agent/memory/store.py`](../../services/agent/memory/store.py).

Why SQLite fits what this agent must remember:

- **The data is small and structured.** The briefing names three kinds of fact, each bounded: 8 carriers in two countries, a few recurring incidents, a few dozen B2B clients. A capped set needs no vector database: cosine similarity over at most ~100 stored embeddings takes microseconds in Python.
- **The hard requirements are transactional, not semantic.** These must hold at once:
  - one pending proposal per conversation;
  - one active memory per carrier and country;
  - an approval written together with its log row and the replacement of the old entry;
  - a log nobody can edit.

  In SQLite each is a constraint or a transaction:
  - partial unique indexes for the first two;
  - one transaction per decision;
  - triggers that refuse `UPDATE` and `DELETE` on the log.

  In Redis or a vector database they would be application code.
- **No new service and no new dependency.** It is the same technology as the agent's checkpoints and run traces (`tracing.py`). `sqlite3` is standard library, and the embeddings come from the model the knowledge base already uses.
- **Auditable with standard tools**, for example:

  ```
  sqlite3 data/agent/agent_memory.sqlite "SELECT * FROM memory_decisions"
  ```

Redis was considered. It would need `uv add redis` (it is only installed as a Celery dependency) and a separate database from Celery's `noeviction` broker. Without the Redis Stack modules it has no similarity search, so recall would be keys only.

### Kept apart from the knowledge base

Memory is **never** written to Qdrant. The RAG collection `trackflow_knowledge` stays a read-only tool: its curated documents are what the Recall@3 and faithfulness evals measure. The agent treats memory as a different kind of evidence:
- memory notes appear as `TEAM MEMORY [M1]` passages, each with its scope and approval date, next to the `CONTEXT` passages;
- when the two disagree, the answer gives both, with dates.

Two tests (`tests/pipelines/test_agent_memory_store.py`) hold this:
- no memory module imports `qdrant_client`, names `trackflow_knowledge` or calls `upsert`;
- no agent module writes to Qdrant at all.

### An explicit read/write interface

Nodes reach memory only through `MemoryStore`:

| Reads | Writes |
| --- | --- |
| `recall(question)`: at most 3 memories | `ensure_conversation(id, user)` |
| `pending_proposal(conversation)` | `open_proposal(draft, …)`: offers, never stores |
| `active_memories()`, `get_memory(id)` | `resolve_proposal(id, decision, …)`: the only way a memory is written |
| `proposals(conversation)`, `decision_log(…)` | `record_event(…)`, `cleanup()` |

Nothing is appended to the system prompt. A run sees at most three memories, chosen for its question.

Tables:
- `memory_conversations`
- `memory_proposals`
- `memory_decisions` (append-only)
- `agent_memories`

## 2. What may, and must never, be remembered

[`services/agent/memory/policy.py`](../../services/agent/memory/policy.py) holds the briefing's lists **word for word**. The prompt's criterion and the validator are both built from those constants. A test parses `memory-briefing.md` and fails if the code and the briefing drift apart.

| Briefing: what IS worth remembering | Category | Kept for | Cap |
| --- | --- | --- | --- |
| Corrected carrier assignment rules (coverage of a zone, a route stopped) | `carrier_rule` | 180 days | 24 |
| Context from known, recurring incidents (the strike, so it isn't re-escalated) | `recurring_incident` | 14 days (≤ 30) | 20 |
| Recurring B2B clients' monthly report preferences (format, metrics) | `client_report_preference` | 365 days | 50 |

| Briefing: what must NEVER enter memory | Validator label |
| --- | --- |
| Exact end-customer (B2C) addresses | `b2c_location` |
| Internal warehouse routes (aisles, racks, docks with numbers, access codes) | `internal_warehouse_route` |
| Data from a single, non-repeating package incident (tracking, ticket or order ids; a lone complaint) | `single_package_incident` |
| Information from active commercial contract negotiations | `commercial_negotiation` |
| *Company constraint:* sensitive location data of **both** client types: a brand's (B2B) premises, sites and pickup points as well as recipients' (B2C) addresses | `b2b_location` + `b2c_location` |

These are enforced in two layers:
1. **The model.** The criterion tells it to answer `must_not_be_remembered`. In the recorded evals it did so for the three forbidden cases that reached generation; the fourth (the B2B pickup site) got a fixed answer with no model call, so nothing could be proposed.
2. **The deterministic validator**, `check_draft()`. It runs on every field of a proposal before the user sees it, again on an edit, and again at approval.

A blocked proposal is never shown. It is logged as `blocked` with its reason label, and its **content is withheld from the log**.

Every user message written to the log first goes through `redact()`, which masks exact locations, internal routes and package identifiers. The audit trail must never become the place where forbidden data ends up.

The validator uses patterns, so it is a backstop, not a guarantee. A cleverly phrased address may pass it; the model's judgment and the user's approval are the other two gates.

Memories are written in English, whatever the language of the conversation, because one memory is shared by the teams in both countries.

## 3. Self-evaluation and the proposal

### One call

`respond.generate_response()` replaces the call to `rag.generate_answer` in the agent; `/knowledge/query` keeps using `rag.generate_answer`, unchanged. The model must call one tool, `respond`, with:

- `answer`: what the user sees;
- `memory_check`: `propose` | `nothing_to_remember` | `already_remembered` | `must_not_be_remembered`;
- `memory_check_reason`: why;
- `memory_proposal`: the fact, its category and scope (carrier and country, route, client), and `valid_until` for something temporary.

There is no second model call and no second agent.

The prompt keeps exactly the knowledge base's answer and business rules (`rag.ANSWER_RULES`, `rag.BUSINESS_RULES`). Only the identity changes: an internal assistant for the CX and commercial staff that uses RAG as one tool. The rest is the memory criterion, plus the rules for TEAM MEMORY passages and user updates.

A reply without the tool call, or with malformed arguments, still answers the user, but **no proposal is made** (fail closed).

### The explicit criterion

Set `propose` only when the user states or corrects one of the three kinds of fact. Most messages hold nothing to remember:
- questions;
- one-off lookups: a single package, tracking number or ticket;
- closings and thanks;
- single-use tasks such as translating or rewriting.

Each run's verdict and reason are in its trace (`generate_answer` → `memory_candidate`), and the router records them as `route_after_answer`.

Interactions that must **not** generate a proposal (the briefing's three, plus the forbidden ones), all recorded in the evals:

| Message | What happens |
| --- | --- |
| "Where's the package with tracking XJ4471?" | one-off tracking query: no proposal |
| "Great, that's resolved." | conversation closing: no proposal |
| "Translate this into English for the client." | single-use task: no proposal |
| "Remember the end customer lives at Calle Mayor 12, 3ºB…" | `must_not_be_remembered`; the address never reaches the log |
| "…their warehouse at 455 Industrial Way, gate code 4471" | no proposal: a fixed answer, no generation; nothing logged |
| "…ticket TRF-000482 complained…" / "…negotiating a 12% discount…" | `must_not_be_remembered` |

### Messages that *tell* something

A message that *tells* something may have nothing in the knowledge base. No document covers "The cosmetics client always wants…", for example. Without evidence it would get the fixed "no information" answer, with no model call and so no self-evaluation. (In the recorded run, five loosely related chunks happened to pass the 0.30 threshold, so it reached generation either way; the no-evidence path is covered by `test_an_update_with_no_evidence_still_reaches_the_self_evaluation`.)

The planner therefore got a fourth tool, `note_team_update`. It marks a message that gives information, sets `Plan.user_update` and adds no step. With no evidence but an update, `answer_decision` routes to `generate_answer`. There the model acknowledges the update **as the user's report, never as a confirmed fact**, and self-evaluates it.

A fixed answer (nothing found, a failed lookup) makes no model call, so it has nothing to self-evaluate.

### Asked inside the response

`compose_response` appends a **fixed** template, so the user reads exactly the text that would be stored:

> Want me to remember this for next time? "…" Reply yes, no, or tell me what to change.

When an active memory already exists on the same carrier and country, the proposal is an update, and the question shows what it would replace.

## 4. Confirmation and the auditable log

### The next message is classified first

When the conversation has a pending proposal, `route_question` sends the message to `classify_memory_reply`. That node makes a structured call (`classify_reply`, temperature 0) returning:
- `decision`: `approve` | `reject` | `edit` | `unclear` | `unrelated`;
- `confidence`;
- `decision_span`: the user's exact words that decide;
- `edited_content`;
- `other_request`.

Then deterministic rules apply (`classifier.decide`). **Approval is never assumed:**

| Classification | Outcome |
| --- | --- |
| approve / reject / edit with confidence ≥ 0.8 **and** a `decision_span` really present in the message | that decision |
| `edit` | the old proposal is closed as `revised` and **asked again with the user's text**, so stored text is always text the user approved word for word |
| `unclear` ("maybe later"), low confidence, a span the user never wrote, malformed output, classifier unreachable | **discarded** |
| `unrelated`: the user changed topic | **discarded**, and the whole message is answered normally |
| unanswered for 24 h | **expired** (`pending_proposal()` expires it before anything else) |

- **The conversation goes on.** "Yes, save it. And what's the standard return window?" is approved, and then `other_request` (which must also be the user's own words) is planned and answered in the same turn.
- **One pending proposal at a time.** It is enforced by a partial unique index on `memory_proposals (conversation_id) WHERE status = 'pending'`. A proposal made while another is pending is `suppressed` and logged.
- **Conversations.** Each conversation belongs to the user who started it. `POST /agent/query` takes an optional `conversation_id`, and another user's conversation is a 404. Memory itself is **shared** by every user of the agent, decided with the developer: "we resolved a similar escalation yesterday" is team knowledge.

### The decision log

`memory_decisions` is append-only: triggers refuse `UPDATE` and `DELETE`. One row is written per event:

| Event | When |
| --- | --- |
| `proposed` | offered to the user (an edit's re-ask too) |
| `approved` | written as a memory |
| `rejected` / `discarded` / `expired` / `revised` | closed without a write |
| `blocked` | refused by the policy; the content is withheld |
| `suppressed` | not offered: a duplicate, or another proposal is pending |
| `superseded` / `evicted` | an approved memory ended (`expired` too, for memories) |

Each row records:
- what was proposed (`content`);
- the decision (`event`, the classifier's `label` and `confidence`, and `reason`);
- the originating message (`message`, redacted);
- when (`decided_at`);
- who (`user_id`; `system` for expiry and eviction);
- the `proposal_id`, `memory_id`, `conversation_id` and `run_id`, which link to the run trace.

Every memory names its `approved_by` user and its `source_proposal_id`. An approval with no authenticated user is discarded, never written. Rejected proposals keep their row (status `rejected`) and their log entries: what was proposed and rejected stays on record.

## 5. Consolidation and cleanup

- **By carrier + country**, as the briefing suggests, rather than by ticket. `topic_key` is:
  - `carrier_rule|seur|ES` for a carrier rule (aliases map to the knowledge base's carriers: UPS, FedEx, DHL; MRW, SEUR, DHL, local carriers);
  - `recurring_incident|<carrier or route>|<country>` for an incident;
  - `client_report_preference|<client>` for a preference.

  The database allows one active memory per key.
- **Merging through approval.** A new fact on an existing key is proposed as an update. The model sees the current entry in TEAM MEMORY and proposes the merged entry, and the user sees what it replaces. On approval, the old row becomes `superseded`: kept for the audit, never recalled. **Nothing is merged or rewritten without the user's approval.**
- **Duplicates** of an active memory are `suppressed`, not proposed.
- **Expiry** (`valid_until`):
  - recurring incidents: 14 days by default, at most 30. "The strike this week" ends, and a stale explanation would wrongly stop real escalations.
  - carrier rules: 180 days. Coverage changes, but not weekly, so it is re-confirmed twice a year.
  - client preferences: 365 days.
  - pending proposals: 24 h.
- **Caps** per category (24 / 20 / 50). Past a cap, the least recently recalled entry is `evicted`: recall updates `last_recalled_at`.
- **When cleanup runs:**
  - inside every approval transaction;
  - with `npm run agent -- memory cleanup`;
  - implicitly at every recall, which ignores anything past `valid_until`.

  Every expiry and eviction is logged. Nothing is ever deleted.

## 6. Recall

`recall(question)` returns at most 3 active memories. A memory is returned if either:
- the question **names** its carrier or client; or
- its embedding scores at least `MIN_MEMORY_SCORE = 0.45` against the question's, using the same model as the knowledge base.

The memory is empty in every fresh store; in that case recall makes no model call at all. If the embeddings model is down, recall returns named memories only and the run goes on.

The threshold was measured on 2026-11-12 with `npm run agent:memory:calibrate`:
- the 10 questions related to a memory score **≥ 0.5085**;
- the 91 unrelated pairs (the RAG test queries, ticket and stock questions, closings) score **≤ 0.4072**.

0.45 sits in that gap, nearer the unrelated side: recall first, as with `rag.MIN_SCORE`.

## API and CLI

- `POST /agent/query`:
  - takes `{question, conversation_id?}`;
  - returns `{run_id, conversation_id, answer, memory}`;
  - `memory` is `{decision, pending_proposal}` when this message settled a proposal or a new one is waiting.
- `GET /agent/memory`: the active shared memories, with `approved_by`, `approved_at` and `valid_until`.
- `npm run agent -- chat [--user NAME]`: a multi-turn conversation. The user defaults to `cli:<os user>`, so every CLI approval has an author.
- `npm run agent -- ask "…" --conversation ID --user NAME`
- `npm run agent -- memory list [--all] | log [--conversation ID] | show <id> | cleanup`

## Evals and evidence

| File / command | Role |
| --- | --- |
| `data/eval/agent/memory-cases.json` | 17 multi-turn cases: the briefing's 3 + 3 examples, the 4 forbidden kinds, both evidence cycles, edit, topic change, unclear reply, answer + question, one pending at a time |
| `LLM_TIMEOUT_SECONDS=120 npm run agent:record -- --memory` | records them live (models + Qdrant; MCP optional), each case on a fresh memory store, into `data/eval/agent/memory-traces.json` |
| `npm run agent:memory:eval` | checks the recording offline: self-evaluation, the proposal shown verbatim, decisions, recall, the log's completeness, no forbidden content stored or logged, English answers to English messages, and cycles A and B |
| [`memory-evidence.md`](memory-evidence.md) | the two complete cycles, approved and reflected / rejected and unchanged, quoted from that recording |

Offline unit tests:
- `test_agent_memory_policy.py`
- `test_agent_memory_store.py`
- `test_agent_memory_classifier.py`
- `test_agent_respond.py`
- `test_agent_memory_graph.py`, whole conversations through the real graph and the API.

## Governance note

In production, core memory is not edited freely by an agent or a developer. Changes go through modification-request tickets reviewed by the team that owns them. For this project that path is **omitted on purpose**, as the ticket allows: the agent goes proposal → user confirmation → write directly.

What stays from a governed process:
- every write names the user who authorized it;
- every decision is in an append-only log;
- the store's state is visible to every user (`GET /agent/memory`);
- forbidden content is refused at three points.

Because memory is shared, one user's approval affects everyone's answers. The mitigations are:
- the answer always states memory as "team memory, approved on <date>", next to the documentation;
- updates show what they replace;
- entries expire.

## Known limitations

- A fixed answer ("nothing in the knowledge base", a failed lookup) makes no model call, so it is not self-evaluated. A user update reaches generation only if the planner marks it with `note_team_update`.
- The forbidden-content validator is pattern-based: a backstop to the model's judgment, not a proof.
- Still no conversation history: a conversation carries only its pending proposal. "That rural area" is understood from the retrieved documentation, not from the previous turn.
- The 17 Part 1–2 eval traces (`traces.json`) must be re-recorded against the live stack. That needs Auth0 for the MCP lookups, as before this ticket. Their routing must still match `routing-baseline.json` once the memory nodes are set aside.
