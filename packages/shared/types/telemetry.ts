/**
 * Contract between a telemetry event and its row in Supabase's `telemetry_events`.
 *
 * The event is what `POST /telemetry/events` accepts: the same field names as the
 * API's `TelemetryEvent` model and the backoffice's `TelemetryService`. The row is
 * what the API stores. The API applies this mapping in Python
 * (`services/telemetry/service.py::to_row`); `tests/test_telemetry_contract.py`
 * fails if the field lists here and there drift apart.
 */

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

/** Envelope fields copied into `tags`, under their `TelemetryEvent` names. */
export const TELEMETRY_TAG_ENVELOPE_FIELDS = ["sessionId", "userId", "requestId", "schemaVersion"] as const;

/** Context dimensions copied from `properties` into `tags` when the event carries them. */
export const TELEMETRY_TAG_DIMENSIONS = ["warehouse", "branch", "source_system", "client_app", "section"] as const;

/** Columns the API writes; `created_at` is set by the database. */
export const TELEMETRY_ROW_COLUMNS = ["id", "timestamp", "event_type", "payload", "tags"] as const;

export type TelemetryTagDimension = (typeof TELEMETRY_TAG_DIMENSIONS)[number];

export type TelemetryTags = Pick<TelemetryEvent, (typeof TELEMETRY_TAG_ENVELOPE_FIELDS)[number]> &
  Partial<Record<TelemetryTagDimension, unknown>>;

export type TelemetryEventRow = {
  /** The event's `eventId`, so a retried event cannot become a second row. */
  id: string;
  /** UTC; a timestamp without an offset is read as UTC. */
  timestamp: string;
  event_type: string;
  /** `properties`, exactly as sent. */
  payload: Record<string, unknown>;
  /** What events are searched by (GIN index), e.g. `tags @> {"warehouse": "Monterrey"}`. */
  tags: TelemetryTags;
};

const HAS_UTC_OFFSET = /(Z|[+-]\d{2}:?\d{2})$/i;

export function toTelemetryRow(event: TelemetryEvent): TelemetryEventRow {
  const tags: TelemetryTags = {
    sessionId: event.sessionId,
    userId: event.userId,
    requestId: event.requestId,
    schemaVersion: event.schemaVersion,
  };

  for (const key of TELEMETRY_TAG_DIMENSIONS) {
    const value = event.properties[key];
    if (value !== undefined && value !== null) {
      tags[key] = value;
    }
  }

  return {
    id: event.eventId,
    timestamp: HAS_UTC_OFFSET.test(event.timestamp) ? event.timestamp : `${event.timestamp}Z`,
    event_type: event.event_type,
    payload: event.properties,
    tags,
  };
}
