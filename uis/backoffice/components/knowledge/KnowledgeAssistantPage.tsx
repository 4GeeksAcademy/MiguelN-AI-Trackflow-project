"use client";

import { type FormEvent, useId, useRef, useState } from "react";
import { StateMessage } from "@/components/common/StateMessage";
import {
  EXAMPLE_QUESTIONS,
  MAX_QUESTION_LENGTH,
  askKnowledgeBase,
  describeKnowledgeError,
  normaliseQuestion,
} from "@/lib/knowledge";

type Exchange =
  | { state: "idle" }
  | { state: "loading"; question: string }
  | { state: "answered"; question: string; answer: string }
  | { state: "failed"; question: string; message: string };

const primaryButtonClass =
  "inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-[color:var(--brand-primary)] px-5 py-2.5 text-sm font-semibold text-[color:var(--on-brand)] transition hover:bg-[color:var(--brand-secondary)] focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-60";

const chipClass =
  "rounded-full border border-[color:var(--border-soft)] bg-[color:var(--surface)] px-3 py-1.5 text-left text-xs font-semibold text-[color:var(--brand-primary)] transition hover:bg-blue-50 focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-50";

/**
 * Ask TrackFlow's policies a question in plain language (Milestone 7, RAG).
 *
 * For the commercial team on a call with a client: the answer is written by
 * the model from the indexed policies, in a salesperson's voice, with its
 * sources. Loading, failure and answer are three distinct states, so a failed
 * call can never be mistaken for an empty answer.
 */
export function KnowledgeAssistantPage() {
  const [draft, setDraft] = useState("");
  const [exchange, setExchange] = useState<Exchange>({ state: "idle" });
  const [validation, setValidation] = useState<string | null>(null);
  const latestRequest = useRef(0);
  const questionId = useId();
  const hintId = useId();
  const errorId = useId();

  const loading = exchange.state === "loading";

  async function ask(text: string) {
    const question = normaliseQuestion(text);
    if (question === null) {
      setValidation(
        text.trim().length === 0
          ? "Type a question first."
          : `Keep the question under ${MAX_QUESTION_LENGTH} characters.`,
      );
      return;
    }
    setValidation(null);
    const request = ++latestRequest.current;
    setExchange({ state: "loading", question });
    try {
      const answer = await askKnowledgeBase(question);
      if (request === latestRequest.current) setExchange({ state: "answered", question, answer });
    } catch (failure: unknown) {
      if (request === latestRequest.current) {
        setExchange({ state: "failed", question, message: describeKnowledgeError(failure) });
      }
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!loading) void ask(draft);
  }

  function askExample(example: string) {
    setDraft(example);
    void ask(example);
  }

  return (
    <main className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6 md:px-10">
      <header className="mb-6 border-b border-[color:var(--border-soft)] pb-5">
        <p className="mb-2 text-sm font-medium text-slate-600">Commercial team</p>
        <h1 className="text-2xl font-semibold leading-tight">Knowledge assistant</h1>
        <p className="mt-2 text-sm text-slate-600">
          Ask about delivery SLAs, returns, carrier coverage or storage pricing. Answers come only from
          TrackFlow&apos;s standard agreements and name their sources.
        </p>
      </header>

      <form onSubmit={submit} noValidate className="flex flex-col gap-3">
        <label htmlFor={questionId} className="text-sm font-semibold">
          Your question
        </label>
        <textarea
          id={questionId}
          name="question"
          rows={3}
          maxLength={MAX_QUESTION_LENGTH}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            // Enter asks, Shift+Enter starts a new line.
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
          aria-describedby={validation ? `${hintId} ${errorId}` : hintId}
          aria-invalid={validation ? true : undefined}
          placeholder="e.g. Which carrier best covers rural Aragón?"
          className="w-full resize-y rounded-xl border border-[color:var(--border-soft)] bg-[color:var(--surface)] px-4 py-3 text-base text-[color:var(--foreground)] outline-none placeholder:text-slate-400 focus:border-[color:var(--brand-primary)] focus:ring-2 focus:ring-[color:var(--brand-primary)]/20"
        />
        <p id={hintId} className="text-xs text-slate-500">
          Enter to ask, Shift+Enter for a new line.
        </p>
        {validation ? (
          <p id={errorId} className="text-sm font-semibold text-red-700">
            {validation}
          </p>
        ) : null}
        <div>
          <button type="submit" className={primaryButtonClass} disabled={loading} aria-busy={loading}>
            {loading ? (
              <>
                <span
                  aria-hidden="true"
                  className="h-4 w-4 animate-spin rounded-full border-2 border-[color:var(--on-brand)]/40 border-t-[color:var(--on-brand)]"
                />
                Looking it up…
              </>
            ) : (
              "Ask"
            )}
          </button>
        </div>
      </form>

      <section aria-labelledby="examples-heading" className="mt-6">
        <h2 id="examples-heading" className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          Common questions
        </h2>
        <ul className="mt-2 flex flex-wrap gap-2">
          {EXAMPLE_QUESTIONS.map((example) => (
            <li key={example}>
              <button type="button" className={chipClass} disabled={loading} onClick={() => askExample(example)}>
                {example}
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section aria-label="Answer" aria-live="polite" aria-busy={loading} className="mt-8">
        {exchange.state === "loading" ? (
          <StateMessage tone="info">
            Searching TrackFlow&apos;s policies for: <q className="font-semibold">{exchange.question}</q>
          </StateMessage>
        ) : null}

        {exchange.state === "failed" ? (
          <StateMessage tone="error">
            <p className="font-semibold">No answer this time.</p>
            <p className="mt-1">{exchange.message}</p>
            <button
              type="button"
              onClick={() => void ask(exchange.question)}
              className="mt-3 inline-flex min-h-10 items-center rounded-xl border border-red-300 bg-[color:var(--surface)] px-4 py-2 text-sm font-semibold text-red-700 transition hover:bg-red-100"
            >
              Try again
            </button>
          </StateMessage>
        ) : null}

        {exchange.state === "answered" ? (
          <article className="rounded-2xl border border-[color:var(--border-soft)] bg-[color:var(--surface)] p-5 shadow-[0_12px_32px_-24px_rgba(37,99,235,0.35)]">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">You asked</p>
            <p className="mt-1 text-sm text-slate-700">{exchange.question}</p>
            <p className="mt-4 text-xs font-semibold uppercase tracking-wide text-slate-500">Answer</p>
            <div className="mt-1 whitespace-pre-line text-base leading-relaxed text-[color:var(--foreground)]">
              {exchange.answer}
            </div>
            <p className="mt-4 border-t border-[color:var(--border-soft)] pt-3 text-xs text-slate-500">
              Generated from TrackFlow&apos;s standard agreements. Anything outside them, such as discounts
              or carrier exceptions, still needs the approval the answer names.
            </p>
          </article>
        ) : null}
      </section>
    </main>
  );
}
