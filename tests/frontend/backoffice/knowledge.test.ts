import {
  EXAMPLE_QUESTIONS,
  MAX_QUESTION_LENGTH,
  askKnowledgeBase,
  describeKnowledgeError,
  normaliseQuestion,
} from "@/lib/knowledge";
import { NetworkError } from "@/lib/api-client";
import { clearToken, storeToken } from "@/lib/auth-storage";
import { installBrowser, removeBrowser } from "../shared/support/browser";
import { API, jsonResponse, scriptFetch, textResponse } from "../shared/support/http";

beforeEach(() => { installBrowser(); storeToken("test-session"); });
afterEach(() => { clearToken(); removeBrowser(); });

it("posts the question with the session token and returns the generated answer", async () => {
  const requests = scriptFetch(jsonResponse(200, { answer: "  The standard return window is 30 days.  " }));

  expect(await askKnowledgeBase("What's the standard return window?")).toBe("The standard return window is 30 days.");
  expect(requests).toHaveLength(1);
  expect(requests[0]).toMatchObject({
    url: `${API}/knowledge/query`,
    method: "POST",
    body: { question: "What's the standard return window?" },
  });
  expect(requests[0].headers.Authorization).toBe("Bearer test-session");
  expect(requests[0].headers["Content-Type"]).toBe("application/json");
});

it.each([
  null,
  {},
  { answer: "" },
  { answer: "   " },
  { answer: 42 },
  [{ answer: "x" }],
])("treats a reply without a usable answer as a failure, never an empty answer: %j", async (body) => {
  scriptFetch(jsonResponse(200, body));
  await expect(askKnowledgeBase("question")).rejects.toThrow("cannot read");
});

it("treats a successful HTML reply as a failure", async () => {
  scriptFetch(textResponse(200, "<html>proxy</html>"));
  await expect(askKnowledgeBase("question")).rejects.toThrow("cannot read");
});

it("explains failures in the assistant's own words", async () => {
  scriptFetch(jsonResponse(503, { detail: "The knowledge assistant is unavailable right now." }));
  const unavailable = await askKnowledgeBase("question").catch((error: unknown) => error);

  expect(describeKnowledgeError(unavailable)).toContain("knowledge assistant is unavailable");
  expect(describeKnowledgeError(new NetworkError(new TypeError("Failed to fetch")))).toContain(
    "Could not reach the knowledge assistant",
  );
});

it("trims questions and refuses blank or oversized ones before any request", () => {
  expect(normaliseQuestion("  Which carrier covers rural Aragón?  ")).toBe("Which carrier covers rural Aragón?");
  expect(normaliseQuestion(" \n ")).toBeNull();
  expect(normaliseQuestion("x".repeat(MAX_QUESTION_LENGTH + 1))).toBeNull();
  expect(normaliseQuestion("x".repeat(MAX_QUESTION_LENGTH))).not.toBeNull();
});

it("offers the briefing's example questions, Black Friday included", () => {
  expect(EXAMPLE_QUESTIONS).toContain("What's the standard return window?");
  expect(EXAMPLE_QUESTIONS).toContain("Which carrier best covers rural Aragón?");
  expect(EXAMPLE_QUESTIONS.some((question) => question.includes("Black Friday"))).toBe(true);
});
