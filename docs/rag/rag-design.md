# RAG design: TrackFlow knowledge assistant

Milestone 7. Brief: [`contexts/RAG-briefing.md`](../../contexts/RAG-briefing.md). Ticket: `lead-instr.png`, phases in `stage-todo-1.png` and `stages-todo-2.png`.

## What it is for

Miguel Torres's commercial team (account managers and business development) keeps answering the same client questions:
- what delivery SLA TrackFlow offers;
- how the returns policy works;
- which carriers cover which zones;
- how storage fees are calculated.

The assistant answers those questions in natural language the way a TrackFlow salesperson would on a call: with exact data, citing its sources, and never promising terms that are not in the standard agreements.

Two rules from the ticket shape everything below:

1. **The caller only ever gets a generated answer.** The raw vector search result (chunks, scores, Qdrant objects) never leaves the server.
2. **One function per responsibility.** Preparing data, storing vectors, searching and generating each live in their own function, so any piece can be swapped without touching the others.

## The flow

```
docs/company-knowledge-base/*.en.md
        │  load_source_documents()        data/process/knowledge_base.py
        ▼
  4 documents ──► split_into_chunks() ──► 14 chunks
        │                                   │ embed(chunk.text)   (embeddings model)
        ▼                                   ▼
                     setup() ──► Qdrant collection `trackflow_knowledge` (cosine)
                                              ▲
 question ──► embed(question) ──► retrieve() ─┘  top-5, score ≥ MIN_SCORE
                                      │          data/pipelines/rag.py
                                      ▼
                     build_messages() ──► generate_answer() ──► answer text
                                            (generation model)
query(question) = generate_answer(question, retrieve(question))

POST /knowledge/query {question} ──► rag.query() ──► {answer}   services/knowledge/
Backoffice /knowledge page ──► POST /knowledge/query               uis/backoffice/
```

Step by step:

1. **Load.** `load_source_documents()` reads the four files the briefing lists from `docs/company-knowledge-base/`, in briefing order. They are verbatim copies of the course's `00-general-contexts/trackflow/` files; identical copies also live in `contexts/`, and a test fails if the two ever differ. The files are listed explicitly rather than globbed. A missing or empty document, or a stray `.md` file, is an error, never a silent skip.
2. **Chunk.** `split_into_chunks()` cuts each document into self-contained units (see Chunking).
3. **Embed.** `embed(text)` calls the embeddings model for one text.
4. **Index.** `setup()` embeds every chunk, then creates or recreates the collection and upserts the points.
5. **Retrieve.** At question time, `retrieve()` embeds the question with the same `embed()`, asks Qdrant for the 5 nearest chunks with `score_threshold=MIN_SCORE`, filters again in Python, and returns plain payload dicts. It logs the hits and scores to the `data.rag` logger.
6. **Assemble the prompt.** `build_messages()` puts the system rules first, then the passages, each labelled `[n] source_document — section`, then the question. Scores are not included.
7. **Generate.** `generate_answer()` calls the generation model at temperature 0 and returns its text. It is called even when nothing was retrieved: the prompt then says no passage was relevant, and the model answers that the knowledge base does not have the information.
8. **Answer.** `query()` is the only entry point for consumers. The API and the UI see only its string.

A later LangGraph agent can call `retrieve()` and `generate_answer()` as separate steps without running retrieval twice.

## Where the pieces live

| Responsibility | Function | File |
| --- | --- | --- |
| Corpus | `load_source_documents` | `data/process/knowledge_base.py` |
| Chunking | `split_into_chunks`, `point_id` | `data/process/knowledge_base.py` |
| Embeddings | `embed`, `normalise_for_embedding` | `data/process/knowledge_base.py` |
| Indexing | `setup` (CLI: `npm run rag:setup`, `--dry-run` prints the chunks) | `data/process/knowledge_base.py` |
| Settings | `RagSettings`, `get_rag_settings` | `data/process/knowledge_base.py` |
| Search | `retrieve` | `data/pipelines/rag.py` |
| Prompt assembly | `build_messages`, `SYSTEM_PROMPT` | `data/pipelines/rag.py` |
| Generation | `generate_answer` | `data/pipelines/rag.py` |
| Orchestration | `query` | `data/pipelines/rag.py` |
| HTTP | `POST /knowledge/query` | `services/knowledge/router.py` |
| UI | `/knowledge` ("Knowledge assistant") | `uis/backoffice/components/knowledge/`, `lib/knowledge.ts` |
| Retrieval eval | `evaluate`, `score_query` (CLI: `npm run rag:eval`) | `scripts/evaluate_rag_retrieval.py` |

## Chunking strategy

**The corpus.** Four short English documents of about 150 words each. Each has a single `#` title and no subheadings. The content is a lead-in sentence, bullet or numbered lists, and short paragraphs. Each paragraph states one rule (the SLA and its compensation, the return window, who pays for returns), and each list states one table of facts (service types, carriers per country, the storage rate card).

**The unit: the Markdown block.** Headings cannot delimit sections here, because there is one per document. Fixed-size windows would cut rules in half (for example "5% compensation" separated from "below 90% for two consecutive months"). So the unit is the block: a paragraph or a list, as separated by blank lines in the source. The rules:

- A block is never split, so a sentence, a list item or a rule never ends up in two chunks.
- A lone sentence ending in `:` is an intro. It is merged into the block it introduces, so "works as follows:" stays with its numbered steps and "based on volume occupied, not number of units:" stays with the rate card.
- Hard-wrapped lines are joined. Each list item keeps its own line.
- Each chunk's text starts with the document title ("Returns Policy\n…"). The chunk then reads on its own in a prompt, and its vector carries the topic. Without the title, "The client can request an inventory aging report…" says nothing about storage.
- `section` is the document title, plus the paragraph's inline label when the paragraph opens with one ("Returns Policy › Return costs", "Carrier Coverage › Spain (Zaragoza)"). For an unlabelled paragraph, the source section really is the document title. Labels are capped at 6 words, so an ordinary sentence with a colon is not mistaken for one.

**Result.** 14 chunks of 21 to 87 words each (`npm run rag:setup -- --dry-run` prints them):

| Document | Chunks | Units |
| --- | --- | --- |
| `sla-delivery` | 3 | service types and their times; the 90% SLA and 5% compensation; the high-demand warning |
| `returns-policy` | 5 | the 3-step process; return window; return costs; international returns; no reselling |
| `carrier-coverage` | 3 | United States carriers; Spain carriers; who chooses the carrier |
| `storage-pricing` | 3 | rate card with grace period and long-term fee; aging report; preferential rates via Miguel Torres |

Every document clears the briefing's minimum of 3 chunks. Tests check that every document yields at least 3 chunks, that no chunk ends mid-sentence, and that the chunks put back together equal the document (nothing dropped or duplicated). They also check that the payload has exactly the briefing's fields.

**Payload** (BRIEFING section 3): `company` (`trackflow`), `source_document` (`sla-delivery | returns-policy | carrier-coverage | storage-pricing`), `section`, `language` (`en`), `chunk_index` (from 0 within each document), and `text` (the chunk, used for prompt assembly).

## Embedding practices

**Two different models.** The 4Geeks-provided models are served through an OpenAI-compatible API (the `openai` SDK):

| Role | Setting | Used by |
| --- | --- | --- |
| Embeddings | `EMBEDDING_MODEL` | `embed()` only: every chunk at index time, every question at query time |
| Generation (chat) | `GENERATION_MODEL` | `generate_answer()` only |

`RagSettings` refuses the same ID for both, and the tests assert that `embed()` never calls the chat API and `generate_answer()` never calls the embeddings API. Both models share `LLM_BASE_URL` / `LLM_API_KEY`. `EMBEDDING_BASE_URL` / `EMBEDDING_API_KEY` override them if the embeddings model is served elsewhere.

**The models in use** (4Geeks LiteLLM gateway, `LLM_BASE_URL=https://llm.4geeks.ai/v1`; the `/v1` is required):

| Role | Model ID |
| --- | --- |
| Embeddings | `downtown-miami/openrouter/perplexity/pplx-embed-v1-0.6b` |
| Generation | `downtown-miami/openrouter/openai/gpt-6-luna` |

The IDs are the ones the gateway's `/v1/models` lists for the student key, used without the `litellm/` prefix (with it, the gateway answers 403). The key gives access to one embeddings model and three chat models. `glm-5.3-flash` is listed but the gateway rejects it.

**Gateway latency.** Requests are often fast (under 1 s after a cold first call), but the gateway regularly stalls for 30–120 s. Each model request has a timeout of `LLM_TIMEOUT_SECONDS` (default 30 s, for the API, where a person waits) and 2 retries. For the batch commands, raise it: `LLM_TIMEOUT_SECONDS=120 npm run rag:eval`.

**One `embed()` for both sides.** `setup()` and `retrieve()` both receive the same `embed` function, which is their default `embedder`. A question and a chunk therefore always go through the same model with the same preprocessing. `setup()` embeds exactly `chunk.text`, and a test asserts it.

**Preprocessing** (`normalise_for_embedding`, inside `embed()`):
- Unicode NFC, so "Aragón" typed with a combining accent matches the document's bytes;
- whitespace collapsed, so hard wraps and list layout do not move the vector.

Case and punctuation are kept, because embeddings models read natural text. The chunk text stored in the payload keeps its line breaks for the prompt.

**Vector dimension.** Not hard-coded: `setup()` reads it from the first vector the model returns and refuses mixed sizes. If the dimension or metric of an existing collection differs (a different embeddings model), the collection is recreated. With `pplx-embed-v1-0.6b` it is **1024**.

**Distance metric:** cosine. Embeddings APIs return direction-meaningful vectors, and cosine makes scores comparable across chunks of different lengths. Qdrant normalises the vectors itself for this metric.

**Similarity threshold (`MIN_SCORE` in `data/pipelines/rag.py`).**
- **Why a threshold.** The briefing requires an explicit "not enough information" when nothing relevant exists. A chunk scoring below `MIN_SCORE` is treated as unrelated and never reaches the prompt. With nothing left, the model is told so and says it cannot answer.
- **How it is applied.** Twice: Qdrant's `score_threshold`, and a Python filter, so a hit below it can never pass.
- **How it is tuned.** Absolute cosine scores depend on the embeddings model, so the value is chosen from data rather than borrowed. `scripts/evaluate_rag_retrieval.py` runs the test questions and the out-of-scope questions in `data/eval/test-queries.json`, then reports the lowest score of a correct chunk and the highest score of an unrelated question:
  - **Separable** (every unrelated question below every correct chunk): it suggests their midpoint, rounded down.
  - **Overlapping:** no value rejects the unrelated questions without also dropping correct chunks, so it suggests a **recall-first** value, the lowest correct score rounded down to a 0.05 step.
  
  The generation prompt is the second line of defence: if a weakly related chunk does pass, the model still answers only what the passage states, and says when it does not answer the question.
- **What was measured (2026-11-08, `pplx-embed-v1-0.6b`).**
  - The first 15 test questions reuse the documents' wording. On those alone the two groups separate: correct chunks score 0.517–0.754 and out-of-scope questions at most 0.449, which suggested 0.48.
  - Running real questions at 0.48 exposed the problem. "What delivery times do we commit to for standard shipments?" lost its chunk ("Standard shipping: 3 to 5 business days", 0.444), and both chat models then answered wrongly: one said the information did not exist, the other gave the 90% on-time SLA instead. "Use a carrier we don't normally work with" kept nothing (0.329), so the Carlos Vega rule never reached the prompt.
  - Five questions phrased the way a salesperson asks were added (tag `paraphrase`), plus two business-sounding out-of-scope questions. With them the groups overlap: the lowest correct chunk scores **0.3285** and the highest out-of-scope question **0.4489** ("parental leave policy for TrackFlow employees").
- **Current value: 0.30 (recall-first).** It keeps every correct chunk measured and still drops clearly unrelated questions (password reset 0.20, football 0.09). Four out-of-scope questions still pass (parental leave, revenue, weather, refrigerated storage); the three business-sounding ones were run end to end, and the model answered "the knowledge base does not have enough information". A wrong "no information" about something the documents do cover is the worse error for a salesperson on a call, so recall is preferred.

## Retrieval

- `retrieve(query, *, k=5, min_score=MIN_SCORE)` returns at most `k` plain dicts, best first: the payload plus `score`. It may return fewer than `k`, or none.
- k = 5 because the corpus is 14 chunks. A question like "how does storage pricing work" legitimately spans 2–3 chunks of one document, and 5 leaves room for a second document without flooding the prompt.
- Failures (Qdrant unreachable, collection missing) raise `KnowledgeBaseError`, which carries no URL or key.

## Generation

The prompt (`SYSTEM_PROMPT`) sets the voice the briefing asks for: TrackFlow's commercial assistant answering the way a salesperson would on a client call. It is courteous and precise, leads with the direct answer, and gives conditions next. It answers in the question's language without translating figures, and ends with a `Sources:` line (`source document — section`), which makes every answer traceable to its source.

**Faithfulness** (BRIEFING section 4):
- Facts come only from the passages.
- Every number, percentage, price, currency, weight, time frame and name is copied exactly: no rounding, conversion or combining.
- Temperature is 0.
- The system prompt itself contains no digits (a test enforces this), so no figure can come from anywhere but a retrieved chunk.

**Business rules** (BRIEFING sections 4 and 6). They are stated in the system prompt, and they apply even if the client insists:

| Briefing rule | Prompt instruction |
| --- | --- |
| No SLA promise on declared high-demand dates | Never promise or guarantee an SLA for Black Friday, Christmas or the January Sales in Spain. Whenever delivery times or the SLA come up, add that it is not guaranteed on those dates, with the conditions the passages give. |
| International returns are never "automatic" | Always say they are handled manually by Sofía Ramos's Returns team. |
| No storage discount without Miguel Torres | Never offer or promise one. Any storage discount requires Miguel Torres's approval. |
| Undocumented carrier exceptions need approval | Never invent a carrier condition. An exception requires approval from Carlos Vega (Head of Carrier Operations). |
| Not enough information means saying so | Say the knowledge base does not have enough information and that it must be confirmed with the responsible team. |

Generation failures and empty model output raise `KnowledgeBaseError`. There is never a fallback to raw chunk text.

**Choosing the generation model (2026-11-08).** The two working chat models on the 4Geeks key were given the same retrieved context (real `retrieve()` at 0.30, real `generate_answer()`) for 9 questions: the four business rules, standard delivery times, a Spanish question and three business-sounding out-of-scope questions. Every figure in every answer was checked against the documents.

| Question | `gpt-6-luna` | `deepseek-v4-flash` |
| --- | --- | --- |
| Guarantee 24 h delivery in Black Friday week? | No; up to +40%; tell clients in advance ✓ | Same ✓ |
| Return from Canada automatic? | No; manual, Sofía Ramos's team ✓ | Same ✓ |
| 20% storage discount for a 12-month deal? | No; needs Miguel Torres; > 50 m³ condition ✓ | No; needs Miguel Torres ✓ (omits the 50 m³ condition) |
| A carrier we don't normally use? | System assigns; exception needs Carlos Vega ✓ | Same ✓, plus "I can bring this request to him", which is not in the documents |
| Standard delivery times? | 3 to 5 business days, plus the peak-date caveat ✓ | Same ✓ |
| ¿Cuánto cuesta el almacenamiento en Zaragoza? | In Spanish: 16 EUR/m³/month, 30 free days, 150% from day 181, discount needs Miguel Torres ✓ | Same figures ✓ |
| Refrigerated storage / parental leave / revenue | "Not enough information, confirm with the team" ✓ | Same ✓, plus "such as Human Resources", which is not in the documents |

Both follow every briefing rule. `gpt-6-luna` was chosen because it adds nothing the documents do not say and keeps the conditions (the 50 m³ threshold). Response times (1–70 s) varied with the gateway's stalls, not with the model.

At the earlier threshold of 0.48, the same "standard delivery times" question reached the model without its chunk: `gpt-6-luna` said the knowledge base did not specify them, and `deepseek-v4-flash` answered with the 90% on-time SLA instead. That run is why the threshold is recall-first (see Embedding practices).

## API

`POST /knowledge/query`, with a bearer token, like `/reporting`: the assistant is internal, and each question costs two model calls.

- Request: `{"question": "..."}`. The question is trimmed, 1–1000 characters, and other fields are refused (422).
- Response 200: `{"answer": "..."}`, the model's string only.
- 503 `{"detail": "The knowledge assistant is unavailable right now. Please try again in a moment."}` when Qdrant or a model is down or not configured. The cause goes to the `api.knowledge` log only.
- The router imports `query()` and nothing else from the RAG stack; a test checks its code for retrieval, generation, Qdrant or OpenAI names.

## UI

`/knowledge` in `uis/backoffice` ("Knowledge assistant" in the nav):
- A question box: Enter asks, Shift+Enter adds a new line.
- Four example questions from the briefing (return window, rural Aragón, storage fees, Black Friday).
- Three distinct states:
  - **loading:** a busy button and "Searching TrackFlow's policies for: …";
  - **failure:** a red panel with the reason and *Try again*;
  - **answer:** your question, then the answer with its sources.

A failed call can never look like an empty answer: `lib/knowledge.ts` treats a reply without a non-empty `answer` as an error. Light and dark mode follow the operating system for the whole backoffice (see `uis/backoffice/app/globals.css`).

## Idempotency

`setup()` can be re-run at will:

- **Deterministic point IDs:** `uuid5(namespace, "<source_document>/<chunk_index>")`, so a re-run overwrites the same points instead of adding new ones.
- **Stale points removed:** after the upsert, every point this run did not write is deleted, for example when a document now yields fewer chunks.
- **Model change handled:** a different vector size or metric recreates the collection.
- **Model failure is harmless:** every chunk is embedded before Qdrant is touched, so a model failure leaves the collection as it was.

Deterministic IDs were chosen over clear-and-reload because the collection is never empty while it is being re-indexed.

## Evaluation

`data/eval/test-queries.json` holds 20 questions covering all four documents, plus 6 out-of-scope questions:
- three questions are about high-demand peaks (Black Friday, January Sales, Christmas);
- five are tagged `paraphrase`, phrased the way a salesperson asks rather than in the documents' wording;
- two of the out-of-scope questions sound like business (revenue, refrigerated storage). A question is a hit when one of its top 3 chunks comes from the expected document and contains the expected phrase. Matching on a phrase keeps the test set valid if chunk numbering changes. A test checks that every phrase points at exactly one shipped chunk.

`npm run rag:eval` reports Recall@3 twice and writes `data/eval/rag/retrieval_eval.json`:
- on the ranking alone;
- counting only correct chunks that also clear `MIN_SCORE`, which is what `query()` really sees. This one is the gate; the target is ≥ 80%.

It exits 0 when the target is met, 1 below it, and 2 when it could not run.

**Results (2026-11-08, `pplx-embed-v1-0.6b`, `MIN_SCORE` 0.30, Qdrant 1.20 in Docker):**

| Measure | Value |
| --- | --- |
| Recall@3, ranking only | **100%** (20/20) |
| Recall@3 with `MIN_SCORE` | **100%** (20/20), target ≥ 80% |
| Correct chunk at rank 1 | 17 of 20 (2 at rank 2, 1 at rank 3) |
| Lowest correct score | 0.3285 (`car-04`, paraphrased carrier exception) |
| Highest out-of-scope score | 0.4489 (`oos-01`, parental leave) |
| Out-of-scope questions passing 0.30 | 4 of 6 (parental leave, revenue, weather, refrigerated storage). The three business-sounding ones were run end to end, and both models answered "not enough information" (see Generation). |

At the earlier candidate of 0.48, Recall@3 with the threshold fell to 90%. The two paraphrased misses were the "3 to 5 business days" chunk and the Carlos Vega chunk. The full per-question output is in `data/eval/rag/retrieval_eval.json`.

## Tests

- `python -m pytest tests/pipelines/test_rag.py`: `retrieve()` against an in-memory Qdrant and a stub client that ignores the threshold; `query()` with `retrieve()` and the LLM mocked; prompt assembly.
- `tests/pipelines/test_knowledge_base.py`: corpus, chunking, `embed()`, settings, `setup()` idempotency.
- `tests/pipelines/test_rag_retrieval_eval.py`: the test set and the scoring.
- `tests/test_knowledge_api.py`: the endpoint.
- `tests/frontend/backoffice/knowledge.test.ts`: the UI's API module.

An autouse fixture replaces every real client with one that fails, so no test can reach Qdrant or a model, even with real keys in `.env`.

## Running it

```bash
docker compose up -d qdrant          # Qdrant 1.20 on 127.0.0.1:6333 (dashboard: /dashboard)
# .env: EMBEDDING_MODEL, GENERATION_MODEL, LLM_BASE_URL, LLM_API_KEY (see .env.example)
npm run rag:setup                    # index (re-runnable); add -- --dry-run to only print chunks
npm run rag:eval                     # Recall@3 + threshold evidence
npm run api                          # POST /knowledge/query
npm run dev:backoffice               # http://localhost:3001/knowledge
```

## Known limitations

- **Geography.** The knowledge-base documents place TrackFlow in the United States (Los Angeles) and Spain. `contexts/context.md` says Mexico (Monterrey). Answers follow the indexed documents, because faithfulness is measured against them.
- **English only.** The corpus and payloads are English (`language: en`). Spanish questions rely on the embeddings model being multilingual, and the test set does not measure them yet.
- **Generic section names.** Paragraphs without an inline label carry only the document title as `section`, because the source documents have no subheadings.
- **Static corpus.** Re-run `setup()` after changing a document. Nothing watches the folder.
