/**
 * Frontend telemetry: events are queued locally and sent to the ingest
 * endpoint in batches, never one request per event. The only entry point for
 * callers is `track()`.
 */

export const TELEMETRY_SCHEMA_VERSION = "1.0.0";
export const CLIENT_APP = "backoffice";

export const FLUSH_INTERVAL_MS = 10_000;
export const FLUSH_THRESHOLD = 20;
export const MAX_BATCH_SIZE = 100; // Mirrors the ingest endpoint's cap.
export const MAX_QUEUE_SIZE = 500;
export const MAX_RETRIES = 3;
export const RETRY_BASE_DELAY_MS = 1_000;

const SESSION_STORAGE_KEY = "trackflow.telemetry.session_id";
const EVENT_TYPE_PATTERN = /^[a-z]+(_[a-z]+)+$/;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export type TelemetryEvent = {
  eventId: string;
  timestamp: string;
  sessionId: string;
  userId: string | null;
  event_type: string;
  schemaVersion: string;
  requestId: string;
  properties: Record<string, unknown>;
};

type DropReason = "buffer_full" | "schema_rejected" | "transport_failed";

export type TelemetryOptions = {
  /** Empty or missing disables capture entirely. */
  endpoint?: string;
  fetchImpl?: typeof fetch;
  sendBeacon?: (url: string, data: Blob) => boolean;
  uuid?: () => string;
  now?: () => Date;
  /** Waits between retries; injectable so tests do not need real time. */
  sleep?: (ms: number) => Promise<void>;
};

function defaultUuid(): string {
  return globalThis.crypto.randomUUID();
}

export class TelemetryService {
  private readonly endpoint: string;
  private readonly fetchImpl: typeof fetch | undefined;
  private readonly beacon: ((url: string, data: Blob) => boolean) | undefined;
  private readonly uuid: () => string;
  private readonly now: () => Date;
  private readonly sleep: (ms: number) => Promise<void>;

  private queue: TelemetryEvent[] = [];
  private userId: string | null = null;
  private sessionId: string | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private flushing = false;
  private dropped = new Map<DropReason, number>();
  private lastDropReport = 0;

  constructor(options: TelemetryOptions = {}) {
    this.endpoint = options.endpoint?.trim() ?? "";
    this.fetchImpl = options.fetchImpl ?? (typeof fetch === "function" ? fetch.bind(globalThis) : undefined);
    this.beacon =
      options.sendBeacon ??
      (typeof navigator !== "undefined" && typeof navigator.sendBeacon === "function"
        ? navigator.sendBeacon.bind(navigator)
        : undefined);
    this.uuid = options.uuid ?? defaultUuid;
    this.now = options.now ?? (() => new Date());
    this.sleep = options.sleep ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
  }

  get enabled(): boolean {
    return this.endpoint !== "";
  }

  get queueLength(): number {
    return this.queue.length;
  }

  setUserId(userId: string | null): void {
    this.userId = userId;
  }

  /** One id per browser tab session; there is no server-side session. */
  getSessionId(): string {
    if (this.sessionId) {
      return this.sessionId;
    }

    let stored: string | null = null;
    try {
      stored = typeof sessionStorage === "undefined" ? null : sessionStorage.getItem(SESSION_STORAGE_KEY);
    } catch {
      // Storage can be blocked; the in-memory id still works for this page.
    }

    this.sessionId = stored ?? this.uuid();

    if (!stored) {
      try {
        if (typeof sessionStorage !== "undefined") {
          sessionStorage.setItem(SESSION_STORAGE_KEY, this.sessionId);
        }
      } catch {
        // See above.
      }
    }

    return this.sessionId;
  }

  newRequestId(): string {
    return this.uuid();
  }

  /** Queues an event; a request goes out only when the batch threshold or the interval is reached. */
  track(eventType: string, properties: Record<string, unknown> = {}, requestId?: string): void {
    if (!this.enabled) {
      return;
    }

    if (!EVENT_TYPE_PATTERN.test(eventType) || typeof properties !== "object" || properties === null) {
      this.recordDrop("schema_rejected", 1);
      return;
    }

    if (this.queue.length >= MAX_QUEUE_SIZE) {
      this.recordDrop("buffer_full", 1);
      return;
    }

    this.queue.push({
      eventId: this.uuid(),
      timestamp: this.now().toISOString(),
      sessionId: this.getSessionId(),
      userId: this.userId,
      event_type: eventType,
      schemaVersion: TELEMETRY_SCHEMA_VERSION,
      requestId: requestId ?? this.uuid(),
      properties,
    });

    this.ensureTimer();

    if (this.queue.length >= FLUSH_THRESHOLD) {
      void this.flush();
    }
  }

  /** Sends everything queued, in batches of at most `MAX_BATCH_SIZE`. */
  async flush(): Promise<void> {
    if (this.flushing || !this.enabled || !this.fetchImpl) {
      return;
    }

    this.flushing = true;
    try {
      while (this.queue.length > 0) {
        const batch = this.queue.slice(0, MAX_BATCH_SIZE);
        const delivered = await this.sendWithRetry(batch);
        this.queue.splice(0, batch.length);

        if (!delivered) {
          this.recordDrop("transport_failed", batch.length);
          // Stop here; the next interval tick tries the remaining events again.
          break;
        }
      }
    } finally {
      this.flushing = false;
      this.reportDrops();
    }
  }

  /** Best-effort delivery while the page is going away; no retries are possible. */
  flushOnHide(): void {
    if (!this.enabled || !this.beacon || this.queue.length === 0) {
      return;
    }

    this.reportDrops();

    while (this.queue.length > 0) {
      const batch = this.queue.slice(0, MAX_BATCH_SIZE);
      // text/plain keeps the beacon a CORS-simple request.
      const body = new Blob([JSON.stringify({ events: batch })], { type: "text/plain" });

      if (!this.beacon(this.endpoint, body)) {
        return;
      }

      this.queue.splice(0, batch.length);
    }
  }

  /** Attaches the page-lifecycle listeners that flush when the tab is hidden or closed. */
  attachPageLifecycle(): void {
    if (typeof document === "undefined" || typeof window === "undefined") {
      return;
    }

    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") {
        this.flushOnHide();
      }
    });
    window.addEventListener("pagehide", () => this.flushOnHide());
  }

  private ensureTimer(): void {
    if (this.timer !== null) {
      return;
    }

    this.timer = setInterval(() => {
      void this.flush();
    }, FLUSH_INTERVAL_MS);
  }

  private async sendWithRetry(batch: TelemetryEvent[]): Promise<boolean> {
    const body = JSON.stringify({ events: batch });

    for (let attempt = 0; attempt <= MAX_RETRIES; attempt += 1) {
      if (attempt > 0) {
        await this.sleep(RETRY_BASE_DELAY_MS * 2 ** (attempt - 1));
      }

      try {
        const response = await this.fetchImpl!(this.endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body,
          credentials: "omit",
          keepalive: body.length < 60_000,
        });

        if (response.ok) {
          return true;
        }

        // A 4xx means this batch will never be accepted; retrying cannot help.
        if (response.status >= 400 && response.status < 500 && response.status !== 429) {
          return false;
        }
      } catch {
        // Offline or the endpoint is down: retry.
      }
    }

    return false;
  }

  private recordDrop(reason: DropReason, count: number): void {
    this.dropped.set(reason, (this.dropped.get(reason) ?? 0) + count);
  }

  /** Turns accumulated drops into `telemetry_dropped` events, which travel in the next batch. */
  private reportDrops(): void {
    if (this.dropped.size === 0) {
      return;
    }

    const nowMs = this.now().getTime();
    const windowSeconds = Math.max(1, Math.round((nowMs - (this.lastDropReport || nowMs)) / 1000));
    this.lastDropReport = nowMs;

    for (const [reason, count] of this.dropped) {
      if (this.queue.length >= MAX_QUEUE_SIZE) {
        return;
      }

      this.queue.push({
        eventId: this.uuid(),
        timestamp: this.now().toISOString(),
        sessionId: this.getSessionId(),
        userId: this.userId,
        event_type: "telemetry_dropped",
        schemaVersion: TELEMETRY_SCHEMA_VERSION,
        requestId: this.uuid(),
        properties: { emitter: "frontend", reason, dropped_count: count, window_seconds: windowSeconds },
      });
    }

    this.dropped.clear();
  }
}

const telemetry = new TelemetryService({ endpoint: process.env.NEXT_PUBLIC_TELEMETRY_ENDPOINT });

if (telemetry.enabled) {
  telemetry.attachPageLifecycle();
}

export function track(eventType: string, properties: Record<string, unknown> = {}, requestId?: string): void {
  telemetry.track(eventType, properties, requestId);
}

export function setTelemetryUser(userId: string | null): void {
  telemetry.setUserId(userId);
}

/** Reads the `sub` claim without verifying it; `/auth/me` does not return the user id. */
export function userIdFromToken(token: string | null): string | null {
  const claims = readTokenClaims(token);

  return typeof claims?.sub === "string" && UUID_PATTERN.test(claims.sub) ? claims.sub : null;
}

/** Whole seconds since the token's `iat`, or null when the token carries none. */
export function tokenAgeSeconds(token: string | null, nowMs: number = Date.now()): number | null {
  const issuedAt = readTokenClaims(token)?.iat;

  return typeof issuedAt === "number" ? Math.max(0, Math.round(nowMs / 1000 - issuedAt)) : null;
}

function readTokenClaims(token: string | null): Record<string, unknown> | null {
  const payload = token?.split(".")[1];
  if (!payload) {
    return null;
  }

  try {
    const base64 = payload.replace(/-/g, "+").replace(/_/g, "/");
    const claims: unknown = JSON.parse(atob(base64.padEnd(Math.ceil(base64.length / 4) * 4, "=")));

    return typeof claims === "object" && claims !== null ? (claims as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

export function getTelemetrySessionId(): string {
  return telemetry.getSessionId();
}

export function newTelemetryRequestId(): string {
  return telemetry.newRequestId();
}
