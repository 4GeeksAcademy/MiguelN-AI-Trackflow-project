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

export async function requestApi(path: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(buildApiUrl(path), {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.headers ?? {}),
    },
  });

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
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

  return response.json();
}
