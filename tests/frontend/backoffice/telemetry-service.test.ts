/**
 * `uis/backoffice/services/telemetry-service.ts`: events are queued and sent
 * in batches, never one request per event.
 */
import {
  FLUSH_INTERVAL_MS,
  FLUSH_THRESHOLD,
  MAX_BATCH_SIZE,
  MAX_QUEUE_SIZE,
  MAX_RETRIES,
  TELEMETRY_SCHEMA_VERSION,
  TelemetryService,
  userIdFromToken,
} from "@/services/telemetry-service";

const ENDPOINT = "http://telemetry.test/telemetry/events";

type Sent = { url: string; events: Array<Record<string, unknown>> };

function setup(overrides: { status?: number; fail?: boolean; beaconOk?: boolean } = {}) {
  const sent: Sent[] = [];
  const beacons: Sent[] = [];
  const sleeps: number[] = [];
  let counter = 0;

  const fetchImpl = jest.fn(async (url: string | URL | Request, init?: RequestInit) => {
    if (overrides.fail) {
      throw new TypeError("offline");
    }
    sent.push({ url: String(url), events: JSON.parse(String(init?.body)).events });
    return new Response("{}", { status: overrides.status ?? 200 });
  }) as unknown as typeof fetch;

  const service = new TelemetryService({
    endpoint: ENDPOINT,
    fetchImpl,
    sendBeacon: (url, data) => {
      void data.text().then((text) => beacons.push({ url, events: JSON.parse(text).events }));
      return overrides.beaconOk ?? true;
    },
    uuid: () => `00000000-0000-4000-8000-${String(++counter).padStart(12, "0")}`,
    now: () => new Date("2026-10-15T10:00:00.000Z"),
    sleep: async (ms) => {
      sleeps.push(ms);
    },
  });

  return { service, fetchImpl, sent, beacons, sleeps };
}

beforeEach(() => {
  jest.useFakeTimers();
});

afterEach(() => {
  jest.clearAllTimers();
  jest.useRealTimers();
});

describe("envelope", () => {
  it("fills every envelope field and keeps the caller's properties", async () => {
    const { service, sent } = setup();
    service.setUserId("11111111-1111-4111-8111-111111111111");

    service.track("section_viewed", { section: "login" });
    await service.flush();

    expect(sent[0].events[0]).toEqual({
      eventId: expect.any(String),
      timestamp: "2026-10-15T10:00:00.000Z",
      sessionId: expect.any(String),
      userId: "11111111-1111-4111-8111-111111111111",
      event_type: "section_viewed",
      schemaVersion: TELEMETRY_SCHEMA_VERSION,
      requestId: expect.any(String),
      properties: { section: "login" },
    });
  });

  it("uses a null userId before login and the supplied requestId when given", async () => {
    const { service, sent } = setup();

    service.track("section_viewed", {}, "22222222-2222-4222-8222-222222222222");
    await service.flush();

    expect(sent[0].events[0]).toMatchObject({ userId: null, requestId: "22222222-2222-4222-8222-222222222222" });
  });

  it("keeps one sessionId for every event", async () => {
    const { service, sent } = setup();

    service.track("section_viewed", {});
    service.track("section_left", {});
    await service.flush();

    const ids = new Set(sent[0].events.map((event) => event.sessionId));
    expect(ids.size).toBe(1);
    expect(ids.has(service.getSessionId())).toBe(true);
  });

  it("makes eventIds unique", async () => {
    const { service, sent } = setup();

    service.track("section_viewed", {});
    service.track("section_viewed", {});
    await service.flush();

    expect(new Set(sent[0].events.map((event) => event.eventId)).size).toBe(2);
  });
});

describe("batching", () => {
  it("sends nothing until the threshold or the interval", () => {
    const { service, fetchImpl } = setup();

    for (let i = 0; i < FLUSH_THRESHOLD - 1; i += 1) {
      service.track("section_viewed", {});
    }

    expect(fetchImpl).not.toHaveBeenCalled();
    expect(service.queueLength).toBe(FLUSH_THRESHOLD - 1);
  });

  it("sends one request carrying the whole queue at the threshold", async () => {
    const { service, fetchImpl, sent } = setup();

    for (let i = 0; i < FLUSH_THRESHOLD; i += 1) {
      service.track("section_viewed", {});
    }
    await jest.advanceTimersByTimeAsync(0);

    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(sent[0].events).toHaveLength(FLUSH_THRESHOLD);
    expect(service.queueLength).toBe(0);
  });

  it("flushes a partial queue as a single request on the interval", async () => {
    const { service, fetchImpl, sent } = setup();

    service.track("section_viewed", {});
    service.track("section_left", {});
    await jest.advanceTimersByTimeAsync(FLUSH_INTERVAL_MS);

    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(sent[0].events.map((event) => event.event_type)).toEqual(["section_viewed", "section_left"]);
  });

  it("never sends an empty batch", async () => {
    const { service, fetchImpl } = setup();

    await service.flush();
    service.track("section_viewed", {});
    await jest.advanceTimersByTimeAsync(FLUSH_INTERVAL_MS * 3);

    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("splits a backlog into batches no larger than the endpoint accepts", async () => {
    const { service, sent } = setup();

    for (let i = 0; i < MAX_BATCH_SIZE + 5; i += 1) {
      service.track("section_viewed", {});
    }
    await service.flush();
    await jest.advanceTimersByTimeAsync(0);

    expect(sent.every(({ events }) => events.length > 1)).toBe(true);
    expect(sent.every(({ events }) => events.length <= MAX_BATCH_SIZE)).toBe(true);
    expect(sent.reduce((total, { events }) => total + events.length, 0)).toBe(MAX_BATCH_SIZE + 5);
  });
});

describe("page hide", () => {
  it("sends the queue with a beacon as one text/plain body", async () => {
    const { service, beacons, fetchImpl } = setup();

    service.track("section_viewed", {});
    service.track("section_left", {});
    service.flushOnHide();
    await jest.advanceTimersByTimeAsync(0);

    expect(fetchImpl).not.toHaveBeenCalled();
    expect(beacons).toHaveLength(1);
    expect(beacons[0].url).toBe(ENDPOINT);
    expect(beacons[0].events).toHaveLength(2);
    expect(service.queueLength).toBe(0);
  });

  it("keeps the events when the browser refuses the beacon", () => {
    const { service } = setup({ beaconOk: false });

    service.track("section_viewed", {});
    service.flushOnHide();

    expect(service.queueLength).toBe(1);
  });
});

describe("failure handling", () => {
  it("retries with exponential backoff, then drops the batch and reports it", async () => {
    const { service, fetchImpl, sleeps } = setup({ fail: true });

    service.track("section_viewed", {});
    await service.flush();

    expect(fetchImpl).toHaveBeenCalledTimes(MAX_RETRIES + 1);
    expect(sleeps).toEqual([1000, 2000, 4000]);
    expect(service.queueLength).toBe(1);
  });

  it("does not retry a batch the endpoint rejects as invalid", async () => {
    const { service, fetchImpl } = setup({ status: 422 });

    service.track("section_viewed", {});
    await service.flush();

    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("retries a 5xx", async () => {
    const { service, fetchImpl } = setup({ status: 503 });

    service.track("section_viewed", {});
    await service.flush();

    expect(fetchImpl).toHaveBeenCalledTimes(MAX_RETRIES + 1);
  });

  it("queues a telemetry_dropped event with the lost count", async () => {
    const { service, sent } = setup({ status: 422 });

    service.track("section_viewed", {});
    service.track("section_left", {});
    await service.flush();
    expect(service.queueLength).toBe(1);

    await service.flush();
    expect(sent).toHaveLength(2);
    expect(sent[1].events[0]).toMatchObject({
      event_type: "telemetry_dropped",
      properties: { emitter: "frontend", reason: "transport_failed", dropped_count: 2 },
    });
  });

  it("drops events beyond the buffer limit and reports the loss", async () => {
    const { service, sent } = setup();

    for (let i = 0; i < MAX_QUEUE_SIZE + 3; i += 1) {
      service.track("section_viewed", {});
    }
    await jest.advanceTimersByTimeAsync(0);
    await service.flush();
    const dropped = sent.flatMap(({ events }) => events).filter((event) => event.event_type === "telemetry_dropped");

    expect(dropped[0].properties).toMatchObject({ emitter: "frontend", reason: "buffer_full", dropped_count: 3 });
  });

  it("rejects an event whose name is not entity_action", () => {
    const { service } = setup();

    service.track("NotValid", {});

    expect(service.queueLength).toBe(0);
  });
});

describe("disabled", () => {
  it("does nothing without an endpoint", () => {
    const service = new TelemetryService({ endpoint: "" });

    service.track("section_viewed", {});

    expect(service.enabled).toBe(false);
    expect(service.queueLength).toBe(0);
  });
});

describe("userIdFromToken", () => {
  const encode = (claims: object) => `h.${Buffer.from(JSON.stringify(claims)).toString("base64url")}.s`;

  it("reads a UUID sub claim", () => {
    expect(userIdFromToken(encode({ sub: "11111111-1111-4111-8111-111111111111" }))).toBe(
      "11111111-1111-4111-8111-111111111111",
    );
  });

  it.each([null, "", "garbage", encode({ sub: "not-a-uuid" }), encode({})])("returns null for %p", (token) => {
    expect(userIdFromToken(token)).toBeNull();
  });
});
