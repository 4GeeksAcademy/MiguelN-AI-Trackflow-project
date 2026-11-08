import { ApiError, readErrorPayload, readJsonBody, sendRequest } from "@/lib/auth-api-client";

const apiBaseUrl = process.env.NEXT_PUBLIC_API_URL?.trim().replace(/\/+$/, "") ?? "";

export function getApiBaseUrl(): string {
  return apiBaseUrl;
}

export function buildApiUrl(path: string): string {
  if (!apiBaseUrl) {
    throw new Error("NEXT_PUBLIC_API_URL is not configured");
  }

  return `${apiBaseUrl}${path.startsWith("/") ? path : `/${path}`}`;
}

/**
 * Call against the external records API. It shares the error classes of the
 * identity client - never its token - so one gate in `lib/friendly-error.ts`
 * can turn either API's failure into something a reader can act on.
 */
export async function requestApi(path: string, init?: RequestInit): Promise<Response> {
  const response = await sendRequest(buildApiUrl(path), {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.headers ?? {}),
    },
  });

  if (!response.ok) {
    throw new ApiError(response.status, await readErrorPayload(response));
  }

  return response;
}

export async function parseResponseJson(response: Response): Promise<unknown | null> {
  if (response.status === 204) {
    return null;
  }

  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    return null;
  }

  return readJsonBody(response);
}
