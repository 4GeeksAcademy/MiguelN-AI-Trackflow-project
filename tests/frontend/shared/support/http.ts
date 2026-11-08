/**
 * A fake `fetch` that answers from a script and records every request, so a
 * test can assert both what the code sent and how it reacted to the answer.
 */

export const API = "http://api.test";

export type RecordedRequest = {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: unknown;
};

/** A JSON answer with the given status. */
export function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** An answer that claims nothing about its content: an HTML error page, say. */
export function textResponse(status: number, body: string): Response {
  return new Response(body, { status, headers: { "content-type": "text/html" } });
}

/**
 * Replace `fetch`. Each request consumes the next scripted answer; an `Error`
 * in the script makes that request fail the way an offline `fetch` does.
 */
export function scriptFetch(...answers: Array<Response | Error>): RecordedRequest[] {
  const requests: RecordedRequest[] = [];

  globalThis.fetch = jest.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    requests.push({
      url: String(input),
      method: init.method ?? "GET",
      headers: { ...(init.headers as Record<string, string> | undefined) },
      body: typeof init.body === "string" ? JSON.parse(init.body) : init.body,
    });

    const answer = answers.shift();
    if (answer === undefined) {
      throw new Error(`Unexpected request to ${String(input)}`);
    }
    if (answer instanceof Error) {
      throw answer;
    }
    return answer;
  }) as typeof fetch;

  return requests;
}
